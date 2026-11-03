#import <AppKit/AppKit.h>
#import <Security/Security.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pwd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include <grp.h>
#include <mach/message.h>
#define REQUIRE(cond, rc)                                                                               \
    do {                                                                                                \
        if (!(cond))                                                                                    \
            return (rc);                                                                                \
    } while (0)
static const char *const prefix = "cmb-zpipe-";

static NSDictionary *consumeRequest(NSString *path, NSString **directory) {
    if (!path.isAbsolutePath || ![path.lastPathComponent isEqualToString:@"launch.json"])
        return nil;
    NSString *dir = path.stringByDeletingLastPathComponent;
    char stdtmp[PATH_MAX], canonicalRoot[PATH_MAX], canonicalDir[PATH_MAX];
    if (!confstr(_CS_DARWIN_USER_TEMP_DIR, stdtmp, sizeof(stdtmp)) || !realpath(stdtmp, canonicalRoot) ||
        !realpath(dir.fileSystemRepresentation, canonicalDir))
        return nil;
    NSString *resolved = [NSString stringWithUTF8String:canonicalDir];
    if (![resolved.stringByDeletingLastPathComponent
            isEqualToString:[NSString stringWithUTF8String:canonicalRoot]] ||
        ![resolved.lastPathComponent hasPrefix:[NSString stringWithUTF8String:prefix]])
        return nil;
    int d = open(canonicalDir, O_DIRECTORY | O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (d < 0)
        return nil;
    struct stat ds, st, name;
    BOOL ok = fstat(d, &ds) == 0 && ds.st_uid == getuid() && (ds.st_mode & 0777) == 0700;
    int f = ok ? openat(d, "launch.json", O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK) : -1;
    ssize_t length = 0;
    char buf[1025] = {0};
    ok = f >= 0 && !fstat(f, &st) && S_ISREG(st.st_mode) && st.st_nlink == 1 && st.st_uid == getuid() &&
         (st.st_mode & 0777) == 0600 && st.st_size > 0 && st.st_size < (off_t)sizeof(buf) &&
         st.st_mtimespec.tv_sec <= time(NULL) && time(NULL) - st.st_mtimespec.tv_sec <= 90;
    if (ok) {
        length = read(f, buf, sizeof(buf));
        ok = length == st.st_size && !fstatat(d, "launch.json", &name, AT_SYMLINK_NOFOLLOW) &&
             name.st_dev == st.st_dev && name.st_ino == st.st_ino && !unlinkat(d, "launch.json", 0);
    }
    if (f >= 0)
        close(f);
    close(d);
    if (!ok)
        return nil;
    id payload = [NSJSONSerialization JSONObjectWithData:[NSData dataWithBytes:buf
                                                                        length:(NSUInteger)length]
                                                 options:0
                                                   error:NULL];
    if (![payload isKindOfClass:NSDictionary.class] || [payload count] != 3)
        return nil;
    NSString *nonce = payload[@"nonce"], *target = payload[@"target"],
             *application = payload[@"application"];
    if (![nonce isKindOfClass:NSString.class] || nonce.length != 64 ||
        [nonce rangeOfCharacterFromSet:[[NSCharacterSet
                                           characterSetWithCharactersInString:@"0123456789abcdef"]
                                           invertedSet]]
                .location != NSNotFound ||
        ![target isKindOfClass:NSString.class] || ![target isEqualToString:@"zcode"] ||
        ![application isKindOfClass:NSString.class])
        return nil;
    *directory = resolved;
    return payload;
}

static NSString *const zcodeIdentifier = @"dev.zcode.app";
static NSString *const brokerRelativePath = @"Resources/gateway/codex-mobile-gateway";

// The request identifies the installation selected in the gateway. Only the vendor
// signature and supported native protocol authorize executing its main Mach-O.
static BOOL verifiedZCode(NSString *bundlePath, NSString **executable) {
    if (!bundlePath.isAbsolutePath || ![bundlePath.pathExtension isEqualToString:@"app"] ||
        ![bundlePath.stringByResolvingSymlinksInPath isEqualToString:bundlePath])
        return NO;
    NSBundle *target = [NSBundle bundleWithPath:bundlePath];
    NSString *image = [bundlePath stringByAppendingPathComponent:@"Contents/MacOS/ZCode"];
    if (![target.bundleIdentifier isEqualToString:zcodeIdentifier] ||
        ![[target objectForInfoDictionaryKey:@"CFBundleShortVersionString"] isEqualToString:@"3.17.2"] ||
        ![target.executablePath isEqualToString:image] ||
        ![image.stringByResolvingSymlinksInPath isEqualToString:image])
        return NO;
    // Reject an executable or bundle replaced through another user's writable directory.
    struct group *admin = getgrnam("admin");
    for (NSString *path = image; path.length > 1; path = path.stringByDeletingLastPathComponent) {
        struct stat info;
        if (lstat(path.fileSystemRepresentation, &info) ||
            (info.st_uid != 0 && info.st_uid != getuid()) || (info.st_mode & S_IWOTH) ||
            ((info.st_mode & S_IWGRP) && !(admin && info.st_uid == 0 && info.st_gid == admin->gr_gid)))
            return NO;
    }
    NSString *rule = @"identifier \"dev.zcode.app\" and anchor apple generic and "
                      "certificate 1[field.1.2.840.113635.100.6.2.6] exists and "
                      "certificate leaf[field.1.2.840.113635.100.6.1.13] exists and "
                      "certificate leaf[subject.OU] = \"8A5X4JJ39T\"";
    SecStaticCodeRef code = NULL;
    SecRequirementRef requirement = NULL;
    BOOL verified =
        SecStaticCodeCreateWithPath((__bridge CFURLRef)target.bundleURL, 0, &code) == errSecSuccess &&
        SecRequirementCreateWithString((__bridge CFStringRef)rule, 0, &requirement) == errSecSuccess &&
        SecStaticCodeCheckValidity(code, kSecCSStrictValidate, requirement) == errSecSuccess;
    if (code)
        CFRelease(code);
    if (requirement)
        CFRelease(requirement);
    if (verified)
        *executable = image;
    return verified;
}

static NSDictionary *signingInfo(SecStaticCodeRef code) {
    CFDictionaryRef info = NULL;
    if (SecCodeCopySigningInformation(code, kSecCSSigningInformation, &info) != errSecSuccess)
        return nil;
    return CFBridgingRelease(info);
}

static BOOL bundledBrokerIsPeer(int socketFD) {
    NSString *helpers = NSBundle.mainBundle.bundlePath.stringByDeletingLastPathComponent;
    NSString *contents = helpers.stringByDeletingLastPathComponent;
    if (![helpers.lastPathComponent isEqualToString:@"Helpers"] ||
        ![contents.lastPathComponent isEqualToString:@"Contents"])
        return NO;
    NSString *outerID =
        [NSBundle bundleWithPath:contents.stringByDeletingLastPathComponent].bundleIdentifier;
    if (![@[ @"io.github.try2love.codexmobilebridge", @"io.github.try2love.codexmobilebridge.test" ]
            containsObject:outerID ?: @""])
        return NO;
    NSString *expected = [contents stringByAppendingPathComponent:brokerRelativePath];
    if (![expected.stringByResolvingSymlinksInPath isEqualToString:expected])
        return NO;
    // The kernel supplies the peer's generation-specific audit token. A PID from a request
    // or a single UID check cannot identify the broker.
    audit_token_t token;
    socklen_t size = sizeof(token);
    if (getsockopt(socketFD, SOL_LOCAL, LOCAL_PEERTOKEN, &token, &size) || size != sizeof(token))
        return NO;
    NSDictionary *attributes = @{
        (__bridge NSString *)kSecGuestAttributeAudit : [NSData dataWithBytes:&token length:sizeof(token)]
    };
    SecCodeRef peer = NULL;
    SecStaticCodeRef live = NULL, bundled = NULL;
    SecRequirementRef requirement = NULL;
    BOOL match = SecCodeCopyGuestWithAttributes(NULL, (__bridge CFDictionaryRef)attributes, 0, &peer) ==
                     errSecSuccess &&
                 SecCodeCopyStaticCode(peer, 0, &live) == errSecSuccess &&
                 SecStaticCodeCreateWithPath((__bridge CFURLRef)[NSURL fileURLWithPath:expected], 0,
                                             &bundled) == errSecSuccess &&
                 SecCodeCopyDesignatedRequirement(bundled, 0, &requirement) == errSecSuccess &&
                 SecCodeCheckValidity(peer, 0, requirement) == errSecSuccess;
    if (match) {
        NSDictionary *running = signingInfo(live), *disk = signingInfo(bundled);
        NSURL *image = running[(__bridge NSString *)kSecCodeInfoMainExecutable];
        NSData *hash = running[(__bridge NSString *)kSecCodeInfoUnique];
        // The broker hashes are placed in the helper's sealed Info.plist after the
        // final broker signing pass, for ad-hoc builds as well as Developer ID.
        NSArray *allowed = [NSBundle.mainBundle objectForInfoDictionaryKey:@"CMBGatewayBrokerCDHashes"];
        match = [expected isEqual:image.path] && [hash isKindOfClass:NSData.class] &&
                [hash isEqual:disk[(__bridge NSString *)kSecCodeInfoUnique]] &&
                [allowed isKindOfClass:NSArray.class] && [allowed containsObject:hash];
    }
    if (requirement)
        CFRelease(requirement);
    if (bundled)
        CFRelease(bundled);
    if (live)
        CFRelease(live);
    if (peer)
        CFRelease(peer);
    return match;
}

static BOOL receiveFds(NSString *dir, NSString *nonce, int out[2]) {
    NSString *path = [dir stringByAppendingPathComponent:@"s"];
    struct stat st;
    if (lstat(path.fileSystemRepresentation, &st) || !S_ISSOCK(st.st_mode) || st.st_uid != getuid() ||
        (st.st_mode & 0777) != 0600)
        return NO;
    struct sockaddr_un sun = {.sun_family = AF_UNIX};
    if (strlcpy(sun.sun_path, path.fileSystemRepresentation, sizeof(sun.sun_path)) >=
        sizeof(sun.sun_path))
        return NO;
    sun.sun_len = SUN_LEN(&sun);
    int s = socket(AF_UNIX, SOCK_STREAM, 0);
    if (s < 0)
        return NO;
    int noSig = 1;
    setsockopt(s, SOL_SOCKET, SO_NOSIGPIPE, &noSig, sizeof(noSig));
    struct timeval timeout = {.tv_sec = 6};
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    uid_t uid;
    gid_t gid;
    BOOL ok = !connect(s, (void *)&sun, sun.sun_len) && !getpeereid(s, &uid, &gid) && uid == getuid() &&
              bundledBrokerIsPeer(s);
    NSData *hello =
        [[NSString stringWithFormat:@"CMB-ZPIPE/1 %@\n", nonce] dataUsingEncoding:NSASCIIStringEncoding];
    if (ok)
        ok = send(s, hello.bytes, hello.length, 0) == (ssize_t)hello.length;
    char tag = 0;
    struct iovec io = {.iov_base = &tag, .iov_len = 1};
    union {
        struct cmsghdr align;
        char bytes[CMSG_SPACE(2 * sizeof(int))];
    } control = {0};
    struct msghdr msg = {.msg_iov = &io,
                         .msg_iovlen = 1,
                         .msg_control = control.bytes,
                         .msg_controllen = sizeof(control.bytes)};
    if (ok)
        ok = recvmsg(s, &msg, 0) == 1 && tag == 'F' && !(msg.msg_flags & (MSG_TRUNC | MSG_CTRUNC));
    struct cmsghdr *c = ok ? CMSG_FIRSTHDR(&msg) : NULL;
    if (!c || c->cmsg_level != SOL_SOCKET || c->cmsg_type != SCM_RIGHTS ||
        c->cmsg_len != CMSG_LEN(sizeof(int) * 2) || CMSG_NXTHDR(&msg, c))
        ok = NO;
    if (ok) {
        int f[2];
        memcpy(f, CMSG_DATA(c), sizeof(f));
        for (int i = 0; i < 2; i++) {
            int flags = fcntl(f[i], F_GETFL);
            if (fstat(f[i], &st) || !S_ISFIFO(st.st_mode) || flags < 0 ||
                (flags & O_ACCMODE) != (i ? O_WRONLY : O_RDONLY))
                ok = NO;
            if (ok) {
                out[i] = fcntl(f[i], F_DUPFD_CLOEXEC, 8);
                if (out[i] < 0)
                    ok = NO;
            }
            close(f[i]);
        }
    }
    close(s);
    return ok;
}

static int start(void) {
    NSString *input = NSProcessInfo.processInfo.environment[@"CMB_ZCODE_LAUNCH_REQUEST"],
             *directory = nil;
    NSDictionary *request = consumeRequest(input, &directory);
    REQUIRE(request, 31);
    NSString *application = request[@"application"], *exe = nil;
    REQUIRE(verifiedZCode(application, &exe), 32);
    REQUIRE([NSRunningApplication runningApplicationsWithBundleIdentifier:zcodeIdentifier].count == 0,
            33);
    int f[2] = {-1, -1};
    REQUIRE(receiveFds(directory, request[@"nonce"], f), 34);
    REQUIRE([NSRunningApplication runningApplicationsWithBundleIdentifier:zcodeIdentifier].count == 0,
            33);
    REQUIRE(dup2(f[0], 3) == 3 && dup2(f[1], 4) == 4, 35);
    close(f[0]);
    close(f[1]);
    // Existing native login bootstrap values needed by AppKit; never pass caller-supplied
    // ZCode, loader, Node, Electron or interpreter switches through this starter.
    struct passwd *pw = getpwuid(getuid());
    REQUIRE(pw, 36);
    NSDictionary *original = NSProcessInfo.processInfo.environment;
    NSMutableDictionary *env = [@{
        @"HOME" : @(pw->pw_dir),
        @"USER" : @(pw->pw_name),
        @"LOGNAME" : @(pw->pw_name),
        @"SHELL" : @(pw->pw_shell),
        @"PATH" : @"/usr/bin:/bin:/usr/sbin:/sbin"
    } mutableCopy];
    char tmp[PATH_MAX];
    if (confstr(_CS_DARWIN_USER_TEMP_DIR, tmp, sizeof(tmp)))
        env[@"TMPDIR"] = @(tmp);
    for (NSString *key in
         @[ @"LANG", @"LC_CTYPE", @"XPC_FLAGS", @"XPC_SERVICE_NAME", @"__CF_USER_TEXT_ENCODING" ])
        if (original[key])
            env[key] = original[key];
    char *envp[20] = {0};
    NSUInteger e = 0;
    for (NSString *key in env)
        envp[e++] = strdup([[NSString stringWithFormat:@"%@=%@", key, env[key]] UTF8String]);
    char *args[] = {(char *)exe.fileSystemRepresentation, "--remote-debugging-pipe", NULL};
    execve(args[0], args, envp);
    return 37;
}
int main(void) {
    @autoreleasepool {
        return start();
    }
}
