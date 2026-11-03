"""Identity of an already-connected Unix peer, bound to its kernel audit token."""

import ctypes as ct
import hashlib
import os
import plistlib
import subprocess
import socket
import struct
import sys
from contextlib import ExitStack
from pathlib import Path

from bridge.platforms.macos.force_exit import Native

P = ct.c_void_p
I = ct.c_uint32
L = ct.c_long


class SignedGatewayPeer:
    """Require the exact executable, UID and signing requirement of this frozen gateway."""

    def __init__(self):
        if not getattr(sys, "frozen", False) or Path(sys.executable).name != "codex-mobile-gateway":
            raise PermissionError("Private ZCode service requires a packaged gateway")
        self.image = str(Path(sys.executable).resolve(strict=True))
        self.native = Native()
        self.sec = ct.CDLL("/System/Library/Frameworks/Security.framework/Security")
        self.cf = ct.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        for name, args in {
            "SecCodeCopySelf": [I, ct.POINTER(P)],
            "SecCodeCopyStaticCode": [P, I, ct.POINTER(P)],
            "SecCodeCopyDesignatedRequirement": [P, I, ct.POINTER(P)],
            "SecCodeCopyGuestWithAttributes": [P, P, I, ct.POINTER(P)],
            "SecCodeCheckValidity": [P, I, P],
            "SecCodeCopySigningInformation": [P, I, ct.POINTER(P)],
            "SecRequirementCreateWithString": [P, I, ct.POINTER(P)],
        }.items():
            f = getattr(self.sec, name)
            f.argtypes, f.restype = args, ct.c_int32
        for name, args, result in (
            ("CFRelease", [P], None),
            ("CFDataCreate", [P, P, L], P),
            ("CFDictionaryCreate", [P, ct.POINTER(P), ct.POINTER(P), L, P, P], P),
            ("CFDictionaryGetValue", [P, P], P),
            ("CFArrayGetCount", [P], L),
            ("CFArrayGetValueAtIndex", [P, L], P),
            ("CFDataGetLength", [P], L),
            ("CFDataGetBytePtr", [P], P),
            ("CFStringCreateWithCString", [P, ct.c_char_p, I], P),
        ):
            f = getattr(self.cf, name)
            f.argtypes, f.restype = args, result
        self.sec.SecCertificateCopyData.argtypes = [P]
        self.sec.SecCertificateCopyData.restype = P
        self.helper_requirement = None
        with ExitStack() as refs:
            code = self._copy(refs, self.sec.SecCodeCopySelf, 0)
            static = self._copy(refs, self.sec.SecCodeCopyStaticCode, code, 0)
            info = self._copy(refs, self.sec.SecCodeCopySigningInformation, static, 2)
            certs = self.cf.CFDictionaryGetValue(info, P.in_dll(self.sec, "kSecCodeInfoCertificates"))
            if (
                not certs
                or self.cf.CFArrayGetCount(certs) < 1
                or self.sec.SecCodeCheckValidity(code, 0, None)
            ):
                raise PermissionError("Gateway must have a valid certificate signature")
            cert = self.sec.SecCertificateCopyData(self.cf.CFArrayGetValueAtIndex(certs, 0))
            if not cert:
                raise PermissionError("Missing gateway certificate")
            refs.callback(self.cf.CFRelease, cert)
            self.leaf_hash = hashlib.sha1(self._bytes(cert)).hexdigest()
            self.cdhash = self._bytes(
                self.cf.CFDictionaryGetValue(info, P.in_dll(self.sec, "kSecCodeInfoUnique"))
            )
            req = P()
            if self.sec.SecCodeCopyDesignatedRequirement(static, 0, ct.byref(req)) or not req:
                raise PermissionError("Missing gateway signature requirement")
            self.requirement = req

    def _copy(self, stack, fn, *args):
        ref = P()
        if fn(*args, ct.byref(ref)) or not ref:
            raise PermissionError("Could not verify signed process")
        stack.callback(self.cf.CFRelease, ref)
        return ref

    def _bytes(self, data):
        if not data:
            raise PermissionError("Missing signing data")
        return ct.string_at(self.cf.CFDataGetBytePtr(data), self.cf.CFDataGetLength(data))

    def verify_starter(self, bundle):
        identifier = "io.github.try2love.codexmobilebridge.zcode-pipe-starter"
        clause = f'identifier "{identifier}" and certificate leaf = H"{self.leaf_hash}"'
        result = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--strict", "-R=" + clause, str(bundle)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=6,
        )
        if result.returncode:
            raise PermissionError("Invalid private ZCode starter signature")
        info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
        if info.get("CFBundleIdentifier") != identifier or self.cdhash not in info.get(
            "CMBGatewayBrokerCDHashes", []
        ):
            raise PermissionError("ZCode starter does not authorize this gateway build")
        if self.helper_requirement is None:
            text = self.cf.CFStringCreateWithCString(None, clause.encode(), 0x08000100)
            if not text:
                raise PermissionError("Invalid ZCode starter requirement")
            try:
                ref = P()
                if self.sec.SecRequirementCreateWithString(text, 0, ct.byref(ref)) or not ref:
                    raise PermissionError("Invalid ZCode starter requirement")
                self.helper_requirement = ref
            finally:
                self.cf.CFRelease(text)

    def close(self):
        if self.helper_requirement:
            self.cf.CFRelease(self.helper_requirement)
            self.helper_requirement = None
        if self.requirement:
            self.cf.CFRelease(self.requirement)
            self.requirement = None

    def require(self, connection, expected_pid, *, starter=None):
        if connection.family != socket.AF_UNIX or (expected_pid is None and starter is None):
            raise PermissionError("Invalid private peer")
        # LOCAL_PEERTOKEN, sys/un.h. audit_token_t includes EUID, PID and pidversion.
        token = connection.getsockopt(0, 6, 32)
        audit = struct.unpack("=8I", token)
        if expected_pid is None:
            expected_pid = audit[5]
        if (
            type(expected_pid) is not int
            or expected_pid < 1
            or audit[1] != os.getuid()
            or audit[5] != expected_pid
        ):
            raise PermissionError("Wrong gateway user or process")
        before = self.native.identity(expected_pid)
        expected_image = str(starter / "Contents/MacOS/CMBZCodePipeStarter") if starter else self.image
        if (
            before["image"] != expected_image
            or before["uid"] != os.getuid()
            or before["zombie"]
            or before["birth"][1] != audit[7]
        ):
            raise PermissionError("Wrong gateway executable or process generation")
        with ExitStack() as refs:
            raw = ct.create_string_buffer(token)
            data = self.cf.CFDataCreate(None, raw, len(token))
            if not data:
                raise PermissionError("Missing audit token")
            refs.callback(self.cf.CFRelease, data)
            keys = (P * 1)(P.in_dll(self.sec, "kSecGuestAttributeAudit").value)
            vals = (P * 1)(data)
            attributes = self.cf.CFDictionaryCreate(None, keys, vals, 1, None, None)
            if not attributes:
                raise PermissionError("Missing code attributes")
            refs.callback(self.cf.CFRelease, attributes)
            guest = self._copy(refs, self.sec.SecCodeCopyGuestWithAttributes, None, attributes, 0)
            requirement = self.helper_requirement if starter else self.requirement
            if not requirement or self.sec.SecCodeCheckValidity(guest, 0, requirement):
                raise PermissionError("Gateway signature changed")
        if self.native.identity(expected_pid) != before:
            raise PermissionError("Gateway process changed")
        return before
