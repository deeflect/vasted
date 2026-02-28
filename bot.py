from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from app.service import get_status, get_usage, start_worker, stop_worker
from app.user_config import load_config


def _authorized(update: Update) -> bool:
    cfg = load_config()
    allowed = cfg.telegram_chat_id
    if not allowed:
        return True
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    user_id = str(update.effective_user.id) if update.effective_user else ""
    return chat_id == allowed or user_id == allowed


async def _reject(update: Update) -> None:
    if update.message:
        await update.message.reply_text("unauthorized")


async def up_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not _authorized(update):
        await _reject(update)
        return
    try:
        r = start_worker()
        await update.message.reply_text(
            "✅ Worker started\n"
            f"• Instance: {r.instance_id}\n"
            f"• Model: {r.model}\n"
            f"• Worker: {r.worker_url}\n"
            f"• Price/hr: ${r.price_per_hour:.4f}"
        )
    except Exception as exc:
        await update.message.reply_text(f"❌ Failed to start worker: {exc}")


async def down_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not _authorized(update):
        await _reject(update)
        return
    try:
        r = stop_worker()
        u = r.usage
        billed = f"${r.billing.billed_cost:.4f}" if r.billing.billed_cost is not None else "unavailable"
        await update.message.reply_text(
            "🛑 Worker stopped\n"
            f"• Requests: {u.requests:,}\n"
            f"• Input tokens: {u.input_tokens:,}\n"
            f"• Output tokens: {u.output_tokens:,}\n"
            f"• Estimated: ${r.billing.estimated_cost:.4f}\n"
            f"• Billed: {billed}"
        )
    except Exception as exc:
        await update.message.reply_text(f"❌ Failed to stop worker: {exc}")


async def status_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not _authorized(update):
        await _reject(update)
        return
    s = get_status()
    if not s.instance_id:
        await update.message.reply_text("🟡 No active worker")
        return
    await update.message.reply_text(
        "🟢 Worker Active\n"
        f"• Instance: {s.instance_id}\n"
        f"• Model: {s.model_name or 'unknown'}\n"
        f"• Worker: {s.worker_url or 'unknown'}\n"
        f"• Price/hr: ${s.price_per_hour:.4f}"
    )


async def usage_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not _authorized(update):
        await _reject(update)
        return
    u = get_usage()
    await update.message.reply_text(
        "📊 Usage\n"
        f"• Requests: {u.requests:,}\n"
        f"• Input tokens: {u.input_tokens:,}\n"
        f"• Output tokens: {u.output_tokens:,}\n"
        f"• Cost: ${u.total_cost:.4f}\n"
        f"• $/1M tokens: ${u.dollars_per_million_tokens:.2f}"
    )


def main() -> None:
    cfg = load_config()
    if not cfg.telegram_token_plain:
        raise SystemExit("telegram_token missing in config; run vasted setup")
    app = Application.builder().token(cfg.telegram_token_plain).build()
    app.add_handler(CommandHandler("up", up_cmd))
    app.add_handler(CommandHandler("down", down_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("usage", usage_cmd))
    app.run_polling()


if __name__ == "__main__":
    main()
