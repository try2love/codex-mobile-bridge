#!/usr/bin/env python3
"""Antigravity 多帳號終端管理小工具

方便在 Linux 電腦端或 SSH 下快速保存、列出、切換與管理 Antigravity 帳戶。
切換帳號時會自動處理系統 Keyring (GNOME Keyring / Secret Service) 與行程重啟，
實現無感秒切，避免卡在登入頁。
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bridge.clients.antigravity.accounts import AntigravityAccounts
from bridge.clients.discovery import discover_clients
from bridge.clients.lifecycle import inspect_client, launch_client, stop_client


def get_accounts():
    storage = ROOT / '.local' / 'desktop-sessions' / 'antigravity-accounts'
    return AntigravityAccounts(storage)


def get_descriptor():
    try:
        clients = discover_clients()
        return clients.get('antigravity')
    except Exception:
        return None


def cmd_list(args):
    acc = get_accounts()
    pub = acc.public()
    rows = pub.get('accounts', [])
    active_id = pub.get('activeId')

    print(f"\n{'='*58}")
    print(f" Antigravity 帳號列表 (當前活躍: {active_id})")
    print(f"{'='*58}")
    if not rows:
        print("尚未有保存的帳號。請先登入帳號後執行: ./scripts/antigravity_account.py save")
        return

    for idx, r in enumerate(rows, start=1):
        is_active = r['id'] == active_id
        marker = "▶ [當前活躍]" if is_active else "  [已保存]  " if r.get('saved') else "  [未保存]  "
        print(f"{idx}. {marker} {r.get('name', '未命名')} ({r.get('email', '無信箱')})")
        print(f"   ID: {r['id']} | 方案: {r.get('tier', 'Google AI')}")
    print(f"{'='*58}\n")


def cmd_save(args):
    name = args.name.strip() if args.name else ''
    acc = get_accounts()
    print("正在保存當前登入之 Antigravity 帳號快照（包含 Keyring 系統憑證）...")
    res = acc.import_current(name)
    active = next((r for r in res['accounts'] if r['id'] == res.get('activeId')), None)
    print(f"✅ 帳號保存成功！")
    if active:
        print(f"   名稱: {active.get('name')}")
        print(f"   信箱: {active.get('email')}")
        print(f"   ID:   {active.get('id')}")


def cmd_switch(args):
    target = args.id_or_num.strip()
    acc = get_accounts()
    pub = acc.public()
    rows = pub.get('accounts', [])

    selected = None
    if target.isdigit():
        idx = int(target) - 1
        if 0 <= idx < len(rows):
            selected = rows[idx]
    if not selected:
        selected = next((r for r in rows if r['id'] == target or r.get('name') == target or r.get('email') == target), None)

    if not selected:
        print(f"❌ 找不到目標帳號: {target}")
        sys.exit(1)

    if selected['id'] == 'current' or not selected.get('saved'):
        print(f"❌ 目標帳號尚未保存，無法切換")
        sys.exit(1)

    if selected['id'] == pub.get('activeId'):
        print(f"ℹ️ 目標帳號已是當前活躍帳號: {selected.get('name')} ({selected.get('email')})")
        return

    print(f"正在切換至: {selected.get('name')} ({selected.get('email')})...")

    desc = get_descriptor()
    was_running = False
    if desc:
        state = inspect_client(desc)
        if state.get('running'):
            was_running = True
            print("   [1/4] 檢查 Antigravity 任務狀態（需無執行中任務）...")
            try:
                from bridge.clients.antigravity.adapter import Antigravity
                sessions_dir = Path.home() / '.local' / 'desktop-sessions' / 'antigravity'
                adapter = Antigravity(sessions_dir)
                adapter.assert_idle()
            except ValueError as err:
                print(f"❌ 切換終止: {err}")
                sys.exit(1)
            except Exception as exc:
                print(f"❌ 檢查任務狀態失敗: {exc}")
                sys.exit(1)

            print("   [2/4] 正在安全關閉 Antigravity 客戶端...")
            stop_client(desc, state=state)

    print("   [3/4] 正在同步置換系統 Keyring 憑證與本地設定檔...")
    token = acc.restore(selected['id'])
    acc.commit(token)

    if was_running and desc:
        print("   [4/4] 正在以 Wayland 環境重新載入 Antigravity...")
        try:
            res = launch_client(desc)
            if not res.get('running'):
                print("⚠️ 提示: Antigravity 重啟響應較慢，請手動確認桌面視窗")
        except Exception as exc:
            print(f"⚠️ 重新啟動提示: {exc}")

    print(f"✅ 帳號切換成功！當前活躍帳號已生效: {selected.get('name')} ({selected.get('email')})")


def cmd_remove(args):
    target = args.id_or_num.strip()
    acc = get_accounts()
    pub = acc.public()
    rows = pub.get('accounts', [])

    selected = None
    if target.isdigit():
        idx = int(target) - 1
        if 0 <= idx < len(rows):
            selected = rows[idx]
    if not selected:
        selected = next((r for r in rows if r['id'] == target), None)

    if not selected:
        print(f"❌ 找不到目標帳號: {target}")
        sys.exit(1)

    acc.remove(selected['id'])
    print(f"✅ 已成功刪除帳號快照: {selected.get('name')} ({selected['id']})")


def main():
    parser = argparse.ArgumentParser(description="Antigravity 多帳號管理 CLI")
    subparsers = parser.add_subparsers(dest="cmd")

    subparsers.add_parser("list", help="列出所有已保存帳號與當前登入")
    
    save_parser = subparsers.add_parser("save", help="保存當前登入帳號為快照")
    save_parser.add_argument("name", nargs="?", default="", help="帳號備註名稱（可選）")

    switch_parser = subparsers.add_parser("switch", help="切換至指定帳號")
    switch_parser.add_argument("id_or_num", help="帳號 ID 或序號（如 1, 2）")

    remove_parser = subparsers.add_parser("remove", help="刪除指定帳號快照")
    remove_parser.add_argument("id_or_num", help="帳號 ID 或序號")

    args = parser.parse_args()
    if not args.cmd or args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "save":
        cmd_save(args)
    elif args.cmd == "switch":
        cmd_switch(args)
    elif args.cmd == "remove":
        cmd_remove(args)


if __name__ == "__main__":
    main()
