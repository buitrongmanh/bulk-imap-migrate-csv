#!/usr/bin/env python3
"""
imap_migrate.py — IMAP Migration & Checker Tool
Đọc danh sách tài khoản từ CSV và thực hiện:
 - check: kiểm tra đăng nhập cho cả source và dest
 - sync: chạy imapsync batch

CSV format (không bắt buộc header, kiểm tra comment '#'):
  email_source, password_source, email_dest, password_dest

Cấu hình IMAP host/port/authuser nằm trong file config.py.

Lưu ý về authuser (proxy auth):
  - Nếu authuser1/authuser2 được điền, cột password trong CSV
    phải là password của authuser (admin), KHÔNG phải password user thường.
  - Cơ chế: SASL PLAIN proxy auth — "login as user, authenticated by admin"
"""

import csv
import subprocess
import logging
import sys
import os
import argparse
import time
import imaplib
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Import CONFIG từ file riêng
from config import CONFIG, IMAPSYNC_FLAGS, IMAPSYNC_FLAGS_GMAIL, TIMEOUT_SEC


# ─────────────────────────────────────────────
#  ANSI Colors & Styles (stdlib only)
# ─────────────────────────────────────────────
class C:
    """ANSI escape codes for terminal colors."""
    _enabled = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    RESET   = "\033[0m"  if _enabled else ""
    BOLD    = "\033[1m"  if _enabled else ""
    DIM     = "\033[2m"  if _enabled else ""
    ITALIC  = "\033[3m"  if _enabled else ""
    UNDERLINE = "\033[4m" if _enabled else ""

    # Foreground
    BLACK   = "\033[30m" if _enabled else ""
    RED     = "\033[31m" if _enabled else ""
    GREEN   = "\033[32m" if _enabled else ""
    YELLOW  = "\033[33m" if _enabled else ""
    BLUE    = "\033[34m" if _enabled else ""
    MAGENTA = "\033[35m" if _enabled else ""
    CYAN    = "\033[36m" if _enabled else ""
    WHITE   = "\033[37m" if _enabled else ""

    # Bright foreground
    BRED    = "\033[91m" if _enabled else ""
    BGREEN  = "\033[92m" if _enabled else ""
    BYELLOW = "\033[93m" if _enabled else ""
    BBLUE   = "\033[94m" if _enabled else ""
    BMAGENTA= "\033[95m" if _enabled else ""
    BCYAN   = "\033[96m" if _enabled else ""
    BWHITE  = "\033[97m" if _enabled else ""

    # Background
    BG_RED    = "\033[41m" if _enabled else ""
    BG_GREEN  = "\033[42m" if _enabled else ""
    BG_YELLOW = "\033[43m" if _enabled else ""
    BG_BLUE   = "\033[44m" if _enabled else ""
    BG_CYAN   = "\033[46m" if _enabled else ""


# ─────────────────────────────────────────────
#  Icons (Unicode)
# ─────────────────────────────────────────────
class Icons:
    MAIL     = "📬"
    ROCKET   = "🚀"
    CHECK    = "✅"
    CROSS    = "❌"
    WARN     = "⚠️ "
    CLOCK    = "⏱️ "
    FOLDER   = "📁"
    KEY      = "🔑"
    SERVER   = "🖥️ "
    ARROW    = "➜"
    SYNC     = "🔄"
    LOG      = "📝"
    GEAR     = "⚙️ "
    SHIELD   = "🛡️ "
    SPARKLE  = "✨"
    PARTY    = "🎉"
    SKULL    = "💀"
    WORKER   = "👷"
    HOURGLASS= "⏳"
    PIN      = "📌"

# ─────────────────────────────────────────────


class ColoredConsoleHandler(logging.StreamHandler):
    LEVEL_STYLES = {
        logging.DEBUG:    (C.DIM,     "DBG"),
        logging.INFO:     (C.BCYAN,   "INF"),
        logging.WARNING:  (C.BYELLOW, "WRN"),
        logging.ERROR:    (C.BRED,    "ERR"),
        logging.CRITICAL: (C.BOLD + C.BRED, "CRT"),
    }

    def emit(self, record):
        try:
            color, tag = self.LEVEL_STYLES.get(record.levelno, (C.WHITE, "???"))
            ts = datetime.now().strftime("%H:%M:%S")
            time_str = f"{C.DIM}{ts}{C.RESET}"
            tag_str  = f"{color}{C.BOLD}[{tag}]{C.RESET}"
            msg = self.format(record)
            prefix = f"  {time_str}  {tag_str}  "
            msg = msg.replace("\n", f"\n{prefix}")
            line = f"{prefix}{msg}"
            self.stream.write(line + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)


def setup_logging(log_dir):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    main_log = Path(log_dir) / f"tool_{ts}.log"

    file_fmt = "%(asctime)s [%(levelname)s] %(message)s"
    console_fmt = "%(message)s"

    file_handler = logging.FileHandler(main_log, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(file_fmt))

    console_handler = ColoredConsoleHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(console_fmt))

    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])
    return logging.getLogger("imap_migrate")


def print_banner(cfg, csv_file, num_accounts, workers, mode_name, dry_run=False):
    w = 58
    print()
    print(f"  {C.BOLD}{C.BCYAN}╔{'═' * w}╗{C.RESET}")
    print(f"  {C.BOLD}{C.BCYAN}║{C.RESET}  {Icons.MAIL}  {C.BOLD}{C.BWHITE}IMAP MIGRATION: {mode_name.upper()}{C.RESET}{'':>{max(1, w - 21 - len(mode_name))}}{C.BOLD}{C.BCYAN}║{C.RESET}")
    print(f"  {C.BOLD}{C.BCYAN}╚{'═' * w}╝{C.RESET}")
    print()
    print(f"  {C.DIM}{'─' * w}{C.RESET}")
    print(f"  {Icons.SERVER} {C.BOLD}Source:{C.RESET}      {C.BWHITE}{cfg['host1']}{C.RESET}{C.DIM}:{cfg['port1']}{C.RESET}  {'🔒' if cfg['ssl1'] else '🔓'}")
    print(f"  {Icons.SERVER} {C.BOLD}Destination:{C.RESET} {C.BWHITE}{cfg['host2']}{C.RESET}{C.DIM}:{cfg['port2']}{C.RESET}  {'🔒' if cfg['ssl2'] else '🔓'}")
    if cfg.get("gmail1") == 1:
        print(f"  {Icons.GEAR}  {C.BOLD}Gmail Mode:{C.RESET}  {C.BG_BLUE}{C.BOLD} ON {C.RESET} {C.DIM}(dùng bộ flag tối ưu cho Gmail source){C.RESET}")
    # [SỬA] Bỏ điều kiện `mode_name == "sync"` → hiển thị authuser ở cả check lẫn sync mode
    if cfg['authuser1']:
        print(f"  {Icons.KEY}  {C.BOLD}Auth Source:{C.RESET} {C.YELLOW}{cfg['authuser1']}{C.RESET}  {C.DIM}(proxy auth — pass CSV = pass admin){C.RESET}")
    if cfg['authuser2']:
        print(f"  {Icons.KEY}  {C.BOLD}Auth Dest:{C.RESET}   {C.YELLOW}{cfg['authuser2']}{C.RESET}  {C.DIM}(proxy auth — pass CSV = pass admin){C.RESET}")
    print(f"  {C.DIM}{'─' * w}{C.RESET}")
    print(f"  {Icons.FOLDER} {C.BOLD}CSV File:{C.RESET}    {C.BWHITE}{csv_file}{C.RESET}")
    print(f"  {Icons.SYNC} {C.BOLD}Accounts:{C.RESET}    {C.BGREEN}{num_accounts}{C.RESET} tài khoản")
    print(f"  {Icons.WORKER}{C.BOLD} Workers:{C.RESET}     {C.BYELLOW}{workers}{C.RESET}{'  (tuần tự)' if workers == 1 else '  (song song)'}")
    if dry_run and mode_name == "sync":
        print(f"  {Icons.WARN}{C.BOLD}{C.BYELLOW} Mode:{C.RESET}       {C.BG_YELLOW}{C.BLACK}{C.BOLD} DRY-RUN {C.RESET} {C.DIM}(không thực thi){C.RESET}")
    print(f"  {C.DIM}{'─' * w}{C.RESET}")
    print()


def _format_duration(seconds):
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s"


def read_csv(csv_path):
    rows = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for lineno, cols in enumerate(reader, start=1):
            if not cols or cols[0].strip().startswith("email_source") or cols[0].strip().startswith("#"):
                continue
            if len(cols) < 4:
                logging.getLogger("imap_migrate").warning(f"Dòng {lineno}: thiếu cột ({cols}), bỏ qua.")
                continue
            rows.append({
                "email_src": cols[0].strip(),
                "pass_src":  cols[1].strip(),
                "email_dst": cols[2].strip(),
                "pass_dst":  cols[3].strip(),
            })
    return rows


# ─────────────────────────────────────────────
# 1. Các hàm xử lý --check
# ─────────────────────────────────────────────

# [SỬA] Thêm tham số `authuser=""` vào signature
def try_imap_login(host, port, use_ssl, username, password, authuser=""):
    """
    Thử đăng nhập IMAP (SSL hoặc PLAIN).

    Nếu authuser được cung cấp, dùng SASL PLAIN proxy authentication:
      - authzid  = username   (tài khoản muốn truy cập — "login as")
      - authcid  = authuser   (tài khoản admin thực hiện auth — "authenticated by")
      - password = password   (mật khẩu của authuser/admin, lấy từ CSV)

    Đây là cơ chế tương tự --authuser1/--authuser2 trong imapsync.
    """
    t0 = time.monotonic()
    result = {
        "status": "FAIL",
        "detail": "",
        "latency_ms": 0,
    }
    try:
        if use_ssl:
            imap = imaplib.IMAP4_SSL(host, port)
        else:
            imap = imaplib.IMAP4(host, port)

        imap.socket().settimeout(TIMEOUT_SEC)

        # [SỬA] Phân nhánh: dùng SASL PLAIN proxy auth nếu có authuser
        if authuser:
            # SASL PLAIN format: "authzid\x00authcid\x00password"
            #   authzid  = username  → server sẽ "act as" user này
            #   authcid  = authuser  → admin account thực hiện authenticate
            #   password = pass của admin (lấy từ cột pass trong CSV)
            # Lưu ý: imaplib sẽ tự động thực hiện base64 encode payload này.
            auth_payload = f"{username}\x00{authuser}\x00{password}"
            
            # imaplib.authenticate nhận callback trả về chuỗi response (RAW, chưa encode)
            imap.authenticate("PLAIN", lambda challenge: auth_payload)
            result["status"] = "OK"
            result["detail"] = f"Đăng nhập thành công (proxy via {authuser})"
        else:
            imap.login(username, password)
            result["status"] = "OK"
            result["detail"] = "Đăng nhập thành công"

        imap.logout()

    except imaplib.IMAP4.error as e:
        err = str(e).lower()
        if any(msg in err for msg in ["authenticationfailed", "invalid credentials", "login failed", "authentication failed"]):
            result["detail"] = "Sai mật khẩu / tài khoản khóa"
        else:
            result["status"] = "WARN"
            result["detail"] = f"IMAP error: {e}"
    except TimeoutError:
        result["status"] = "WARN"
        result["detail"] = "Kết nối timeout"
    except ConnectionRefusedError:
        result["status"] = "WARN"
        result["detail"] = "Từ chối kết nối"
    except OSError as e:
        result["status"] = "WARN"
        result["detail"] = f"Network error: {e}"
    except Exception as e:
        result["status"] = "WARN"
        result["detail"] = f"Lỗi không xác định: {e}"
    finally:
        result["latency_ms"] = int((time.monotonic() - t0) * 1000)
    return result

def run_check(row, cfg, index=0, total=0):
    """Kiểm tra tài khoản source & destination cho 1 dòng CSV."""
    logger = logging.getLogger("imap_migrate")
    progress = f"{C.DIM}[{index}/{total}]{C.RESET}" if total else ""
    
    # [SỬA] Truyền authuser1/authuser2 từ cfg vào try_imap_login
    # Nếu authuser được set, password trong CSV là password của authuser (admin)
    res_src = try_imap_login(
        cfg["host1"], cfg["port1"], cfg["ssl1"],
        row["email_src"], row["pass_src"],
        authuser=cfg.get("authuser1", ""),   # "" = không dùng proxy auth
    )
    res_dst = try_imap_login(
        cfg["host2"], cfg["port2"], cfg["ssl2"],
        row["email_dst"], row["pass_dst"],
        authuser=cfg.get("authuser2", ""),   # "admin@..." = dùng proxy auth
    )

    # In log ngay lập tức
    def _print_res(srv_type, email, res):
        latency = f"{res['latency_ms']:>4}ms"
        if res["status"] == "OK":
            logger.info(f"{progress} {Icons.CHECK} {C.BGREEN}OK  {C.RESET} {srv_type:4} {email:<35} {latency} {C.DIM}{res['detail']}{C.RESET}")
        elif res["status"] == "FAIL":
            logger.error(f"{progress} {Icons.CROSS} {C.BRED}FAIL{C.RESET} {srv_type:4} {email:<35} {latency} {res['detail']}")
        else:
            logger.warning(f"{progress} {Icons.WARN} {C.BYELLOW}WARN{C.RESET} {srv_type:4} {email:<35} {latency} {res['detail']}")

    _print_res("SRC", row["email_src"], res_src)
    _print_res("DST", row["email_dst"], res_dst)

    return {
        "row": row,
        "src": res_src,
        "dst": res_dst
    }


def summary_check(results):
    print("\n" + "=" * 62)
    print("  KẾT QUẢ KIỂM TRA ĐĂNG NHẬP")
    print("=" * 62)
    
    src_fails = []
    dst_fails = []
    src_warns = []
    dst_warns = []

    for r in results:
        if r["src"]["status"] == "FAIL": src_fails.append((r["row"]["email_src"], r["src"]["detail"]))
        elif r["src"]["status"] == "WARN": src_warns.append((r["row"]["email_src"], r["src"]["detail"]))
        
        if r["dst"]["status"] == "FAIL": dst_fails.append((r["row"]["email_dst"], r["dst"]["detail"]))
        elif r["dst"]["status"] == "WARN": dst_warns.append((r["row"]["email_dst"], r["dst"]["detail"]))

    total = len(results) * 2 # 2 account/row
    fails = len(src_fails) + len(dst_fails)
    warns = len(src_warns) + len(dst_warns)
    oks = total - fails - warns

    print(f"  {Icons.CHECK} Đăng nhập thành công: {oks}/{total}")
    print(f"  {Icons.CROSS} Sai mật khẩu / khóa  : {fails}")
    print(f"  {Icons.WARN} Lỗi kết nối / khác   : {warns}")

    if src_fails or dst_fails:
        print("\n─── TÀI KHOẢN CẦN ĐỔI/KIỂM TRA MẬT KHẨU ───")
        for e, err in src_fails: print(f"  {C.BRED}SRC ✘{C.RESET} {e:<35} {C.DIM}{err}{C.RESET}")
        for e, err in dst_fails: print(f"  {C.BRED}DST ✘{C.RESET} {e:<35} {C.DIM}{err}{C.RESET}")

    if src_warns or dst_warns:
        print("\n─── CẢNH BÁO (Lỗi kết nối / server) ───")
        for e, err in src_warns: print(f"  {C.BYELLOW}SRC ⚠{C.RESET} {e:<35} {C.DIM}{err}{C.RESET}")
        for e, err in dst_warns: print(f"  {C.BYELLOW}DST ⚠{C.RESET} {e:<35} {C.DIM}{err}{C.RESET}")
        
    print("=" * 62 + "\n")


# ─────────────────────────────────────────────
# 2. Các hàm xử lý --sync
# ─────────────────────────────────────────────

def build_command(row, cfg):
    cmd = [cfg["imapsync_bin"]]

    # Source
    cmd += ["--host1", cfg["host1"], "--port1", str(cfg["port1"])]
    if cfg["ssl1"]: cmd += ["--ssl1"]
    if cfg["authuser1"]: cmd += ["--authuser1", cfg["authuser1"]]
    cmd += ["--user1", row["email_src"], "--password1", row["pass_src"]]

    # Destination
    cmd += ["--host2", cfg["host2"], "--port2", str(cfg["port2"])]
    if cfg["ssl2"]: cmd += ["--ssl2"]
    if cfg["authuser2"]: cmd += ["--authuser2", cfg["authuser2"]]
    cmd += ["--user2", row["email_dst"], "--password2", row["pass_dst"]]

    if cfg.get("gmail1") == 1:
        cmd += IMAPSYNC_FLAGS_GMAIL
    else:
        cmd += IMAPSYNC_FLAGS
        
    return cmd


def run_sync(row, cfg, log_dir, index=0, total=0):
    email_src = row["email_src"]
    email_dst = row["email_dst"]
    label = f"{email_src} {C.CYAN}{Icons.ARROW}{C.RESET} {email_dst}"
    progress = f"{C.DIM}[{index}/{total}]{C.RESET}" if total else ""

    safe_name = email_src.replace("@", "_at_").replace(".", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    acct_log = Path(log_dir) / f"{safe_name}_{ts}.log"

    cmd = build_command(row, cfg)
    logger = logging.getLogger("imap_migrate")
    
    logger.info(f"{Icons.HOURGLASS} {progress} {C.BOLD}Bắt đầu:{C.RESET} {label}\n   {Icons.LOG} Log: {C.DIM}{acct_log}{C.RESET}")

    start_time = time.time()
    try:
        with open(acct_log, "w", encoding="utf-8") as flog:
            result = subprocess.run(cmd, stdout=flog, stderr=subprocess.STDOUT, universal_newlines=True)
        elapsed = time.time() - start_time
        elapsed_str = _format_duration(elapsed)

        if result.returncode == 0:
            logger.info(f"{Icons.CHECK} {progress} {C.BGREEN}{C.BOLD}Thành công:{C.RESET} {label}  {C.DIM}({elapsed_str}){C.RESET}")
            return email_src, email_dst, True, "OK", elapsed
        else:
            status = f"exit code {result.returncode}"
            logger.error(f"{Icons.CROSS} {progress} {C.BRED}{C.BOLD}Thất bại:{C.RESET} {label}  {C.DIM}({status} • {elapsed_str}){C.RESET}")
            return email_src, email_dst, False, status, elapsed
    except Exception as exc:
        elapsed = time.time() - start_time
        logger.error(f"{Icons.SKULL} {progress} {C.BRED}{C.BOLD}Lỗi:{C.RESET} {label} — {exc}")
        return email_src, email_dst, False, str(exc), elapsed


def summary_sync(results, total_elapsed):
    ok  = [r for r in results if r[2]]
    err = [r for r in results if not r[2]]
    total = len(results)
    w = 62

    print()
    print(f"  {C.BOLD}{C.BCYAN}╔{'═' * w}╗{C.RESET}")
    print(f"  {C.BOLD}{C.BCYAN}║{C.RESET}  {Icons.SPARKLE}  {C.BOLD}{C.BWHITE}KẾT QUẢ MIGRATE{C.RESET}{'':>38}{C.BOLD}{C.BCYAN}║{C.RESET}")
    print(f"  {C.BOLD}{C.BCYAN}╠{'═' * w}╣{C.RESET}")

    ok_pct = (len(ok) / total * 100) if total else 0
    if len(ok) == total: stats_icon, stats_color = Icons.PARTY, C.BGREEN
    elif len(ok) > 0:    stats_icon, stats_color = Icons.WARN, C.BYELLOW
    else:                stats_icon, stats_color = Icons.SKULL, C.BRED

    print(f"  {C.BOLD}{C.BCYAN}║{C.RESET}  {stats_icon} {C.BOLD}Tổng cộng:{C.RESET}  {stats_color}{C.BOLD}{len(ok)}{C.RESET}/{total} thành công ({stats_color}{ok_pct:.0f}%{C.RESET})" + 
          f"{'':>{max(1, w - 35 - len(str(len(ok))) - len(str(total)) - (4 if ok_pct == 100 else 3))}}{C.BOLD}{C.BCYAN}║{C.RESET}")
    print(f"  {C.BOLD}{C.BCYAN}║{C.RESET}  {Icons.CLOCK}{C.BOLD}Thời gian:{C.RESET}  {_format_duration(total_elapsed)}" +
          f"{'':>{max(1, w - 24 - len(_format_duration(total_elapsed)))}}{C.BOLD}{C.BCYAN}║{C.RESET}")
    print(f"  {C.BOLD}{C.BCYAN}╠{'═' * w}╣{C.RESET}")
    print(f"  {C.BOLD}{C.BCYAN}║{C.RESET} {C.BOLD}{C.DIM} #  {'Trạng thái':10} {'Nguồn':28} {'Thời gian':>8}{C.RESET}  {C.BOLD}{C.BCYAN}║{C.RESET}")
    print(f"  {C.BOLD}{C.BCYAN}║{C.RESET} {C.DIM}{'─' * (w - 2)}{C.RESET}  {C.BOLD}{C.BCYAN}║{C.RESET}")

    for i, (es, ed, success, msg, elapsed) in enumerate(results, 1):
        idx = f"{i:>2}"
        dur = _format_duration(elapsed)
        es_s = es if len(es) <= 28 else es[:25] + "..."
        status = f"{C.BGREEN}{Icons.CHECK} Thành công{C.RESET}" if success else f"{C.BRED}{Icons.CROSS} Thất bại {C.RESET}"
        print(f"  {C.BOLD}{C.BCYAN}║{C.RESET} {C.DIM}{idx}{C.RESET}  {status}  {es_s:<28} {C.DIM}{dur:>8}{C.RESET}  {C.BOLD}{C.BCYAN}║{C.RESET}")

    print(f"  {C.BOLD}{C.BCYAN}╚{'═' * w}╝{C.RESET}")

    if err:
        print()
        print(f"  {C.BRED}{C.BOLD}┌── Chi tiết lỗi ──────────────────────────────────────┐{C.RESET}")
        for es, ed, _, msg, _ in err:
            print(f"  {C.BRED}│{C.RESET}  {Icons.CROSS} {C.BOLD}{es}{C.RESET}\n  {C.BRED}│{C.RESET}    {C.DIM}{Icons.ARROW} {ed}{C.RESET}\n  {C.BRED}│{C.RESET}    {C.RED}Lỗi: {msg}{C.RESET}")
        print(f"  {C.BRED}{C.BOLD}└──────────────────────────────────────────────────────┘{C.RESET}")

    logger = logging.getLogger("imap_migrate")
    logger.info(f"TỔNG KẾT SYNC: {len(ok)}/{total} tài khoản thành công ({_format_duration(total_elapsed)})")
    if err:
        for es, ed, _, msg, _ in err: logger.info(f"  THẤT BẠI: {es} → {ed}: {msg}")
    print()


# ─────────────────────────────────────────────
# 3. MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="IMAPSync Batch Migration & Checker Tool")
    parser.add_argument("csv_file", help="File CSV (email_src, pass_src, email_dst, pass_dst)")
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sync", action="store_true", help="Chạy đồng bộ email (imapsync)")
    group.add_argument("--check", action="store_true", help="Kiểm tra thông tin đăng nhập IMAP (source & dest)")

    parser.add_argument("--workers", "-w", type=int, default=CONFIG["max_workers"], help=f"Số luồng song song (mặc định: {CONFIG['max_workers']})")
    parser.add_argument("--dry-run", action="store_true", help="In lệnh imapsync sẽ chạy (chỉ áp dụng với --sync)")
    args = parser.parse_args()

    mode_name = "sync" if args.sync else "check"
    logger = setup_logging(CONFIG["log_dir"])

    if not Path(args.csv_file).exists():
        print(f"\n  {Icons.CROSS} {C.BRED}{C.BOLD}Không tìm thấy file CSV:{C.RESET} {args.csv_file}")
        sys.exit(1)

    rows = read_csv(args.csv_file)
    workers = args.workers
    total = len(rows)

    print_banner(CONFIG, args.csv_file, total, workers, mode_name, args.dry_run)

    if total == 0:
        print(f"  {Icons.WARN}{C.BYELLOW} Không có tài khoản nào trong file CSV.{C.RESET}\n")
        return

    # ── CHECK MODE ──
    if args.check:
        print(f"  {Icons.SHIELD} {C.BOLD}Bắt đầu kiểm tra thông tin đăng nhập...{C.RESET}\n")
        results = []
        if workers == 1:
            for i, row in enumerate(rows, 1):
                results.append(run_check(row, CONFIG, i, total))
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(run_check, row, CONFIG, i, total) for i, row in enumerate(rows, 1)]
                results = [fut.result() for fut in as_completed(futures)]
        summary_check(results)
        
        fails = sum(1 for r in results if r["src"]["status"] != "OK" or r["dst"]["status"] != "OK")
        sys.exit(1 if fails else 0)

    # ── SYNC MODE ──
    if args.sync:
        if args.dry_run:
            print(f"  {C.BYELLOW}{C.BOLD}{'─' * 58}{C.RESET}")
            print(f"  {Icons.GEAR}  {C.BOLD}{C.BYELLOW}Lệnh sẽ được chạy:{C.RESET}\n")
            for i, row in enumerate(rows, 1):
                cmd = build_command(row, CONFIG)
                display = []
                skip = False
                for t in cmd:
                    if skip:
                        display.append(f"{C.RED}****{C.RESET}"); skip = False
                    else:
                        display.append(t)
                    if t in ("--password1", "--password2"):
                        skip = True
                print(f"  {C.DIM}[{i}/{total}]{C.RESET} {Icons.SYNC} {C.BOLD}{row['email_src']}{C.RESET}")
                print(f"        {C.DIM}{' '.join(display)}{C.RESET}\n")
            print(f"  {C.BYELLOW}{C.BOLD}{'─' * 58}{C.RESET}")
            return

        print(f"  {Icons.ROCKET} {C.BOLD}Bắt đầu migrate...{C.RESET}\n")
        results = []
        total_start = time.time()

        if workers == 1:
            for i, row in enumerate(rows, 1):
                results.append(run_sync(row, CONFIG, CONFIG["log_dir"], i, total))
        else:
            logger.info(f"{Icons.WORKER} Chạy song song với {C.BYELLOW}{workers}{C.RESET} worker(s)…")
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(run_sync, row, CONFIG, CONFIG["log_dir"], i, total) for i, row in enumerate(rows, 1)]
                results = [fut.result() for fut in as_completed(futures)]

        total_elapsed = time.time() - total_start
        summary_sync(results, total_elapsed)
        failed = sum(1 for r in results if not r[2])
        sys.exit(1 if failed else 0)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {C.BYELLOW}{Icons.WARN} Đã nhận lệnh dừng (Ctrl+C). Đang thoát ngay lập tức...{C.RESET}\n")
        # Sử dụng os._exit(0) để thoát ngay, không đợi các thread khác đang chạy ngầm
        os._exit(0)