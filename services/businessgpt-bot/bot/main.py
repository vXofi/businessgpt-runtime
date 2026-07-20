from __future__ import annotations

import asyncio
import logging
import shutil

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeChatMember,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    MessageReactionHandler,
    TypeHandler,
    filters,
)

from .businessgpt_client import BusinessGPTClient
from .config import Config
from .crypto import FeedbackEncryptor
from .context_cache import ContextCache
from .db import BotDB
from .media_cache import EncryptedMediaCache
from .limits import GenerationQueue
from .validation import validate_allowed_api_url
from .handlers import (
    authorize_update,
    cancel_command,
    error_handler,
    gpt_command,
    model_delete_command,
    model_new_or_edit_command,
    model_show_command,
    model_use_command,
    models_command,
    media_message,
    prompt_delete_command,
    prompt_new_or_edit_command,
    prompt_reset_command,
    prompt_show_command,
    prompt_test_command,
    prompt_use_command,
    prompts_command,
    reaction_update,
    remember_message,
    settings_callback,
    settings_command,
    start_command,
    status_command,
    text_message,
    tools_command,
)


ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "callback_query",
    "message_reaction",
]

GROUP_COMMANDS = [
    BotCommand("gpt", "Generate a reply"),
]

ADMIN_COMMANDS = [
    BotCommand("gpt", "Generate a reply"),
    BotCommand("settings", "Open chat settings"),
    BotCommand("status", "Show active configuration"),
    BotCommand("cancel", "Cancel pending editor action"),
    BotCommand("prompts", "List prompt presets"),
    BotCommand("prompt_show", "Show a prompt preset"),
    BotCommand("prompt_use", "Select a prompt preset"),
    BotCommand("prompt_new", "Create a prompt preset"),
    BotCommand("prompt_edit", "Edit a prompt preset"),
    BotCommand("prompt_delete", "Delete a prompt preset"),
    BotCommand("prompt_reset", "Use the API default prompt"),
    BotCommand("prompt_test", "Test a prompt preset"),
    BotCommand("models", "List model profiles"),
    BotCommand("model_show", "Show a model profile"),
    BotCommand("model_use", "Select a model profile"),
    BotCommand("tools", "Show tool scaffolds"),
]

OWNER_COMMANDS = [
    *ADMIN_COMMANDS,
    BotCommand("model_new", "Create a model profile"),
    BotCommand("model_edit", "Edit a model profile"),
    BotCommand("model_delete", "Delete a model profile"),
]

logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    config: Config = application.bot_data["config"]
    db: BotDB = application.bot_data["db"]
    db.migrate()
    db.migrate_legacy_prompts(min(config.allowed_chat_ids))
    for model in db.list_model_profiles():
        try:
            validate_allowed_api_url(model["api_url"], config.allowed_api_urls)
        except ValueError:
            logger.warning("quarantining model profile with disallowed endpoint: %s", model["name"])
            db.soft_delete_model_profile(model["name"])
    db.seed_default_model_profile(
        api_url=config.api_url,
        max_tokens=config.default_max_tokens,
        temperature=config.default_temperature,
        top_p=config.default_top_p,
        top_k=config.default_top_k,
        repetition_penalty=config.default_repetition_penalty,
    )
    application.bot_data["media_cache"].cleanup()
    _run_maintenance(application)
    application.bot_data["maintenance_task"] = asyncio.create_task(
        _maintenance_loop(application)
    )
    me = await application.bot.get_me()
    application.bot_data["bot_id"] = me.id
    application.bot_data["bot_username"] = me.username
    try:
        await application.bot.set_my_commands(
            GROUP_COMMANDS,
            scope=BotCommandScopeAllGroupChats(),
        )
        await application.bot.set_my_commands(
            ADMIN_COMMANDS,
            scope=BotCommandScopeAllChatAdministrators(),
        )
        await application.bot.set_my_commands(
            ADMIN_COMMANDS,
            scope=BotCommandScopeAllPrivateChats(),
        )
        for owner_user_id in config.owner_user_ids:
            await application.bot.set_my_commands(
                OWNER_COMMANDS,
                scope=BotCommandScopeChat(chat_id=owner_user_id),
            )
            for chat_id in config.allowed_chat_ids:
                await application.bot.set_my_commands(
                    OWNER_COMMANDS,
                    scope=BotCommandScopeChatMember(
                        chat_id=chat_id,
                        user_id=owner_user_id,
                    ),
                )
    except TelegramError as exc:
        logger.warning("could not register Telegram command menu: %s", exc)
    application.bot_data["generation_queue"].start()
    logger.info(
        "bot initialized username=%s telegram_proxy=%s",
        me.username,
        "configured" if config.telegram_proxy_url else "disabled",
    )


async def post_shutdown(application: Application) -> None:
    api: BusinessGPTClient = application.bot_data["businessgpt"]
    db: BotDB = application.bot_data["db"]
    generation_queue: GenerationQueue = application.bot_data["generation_queue"]
    maintenance_task = application.bot_data.get("maintenance_task")
    if maintenance_task:
        maintenance_task.cancel()
        try:
            await maintenance_task
        except asyncio.CancelledError:
            pass
    await generation_queue.shutdown()
    await api.close()
    db.close()


def _run_maintenance(application: Application) -> None:
    config: Config = application.bot_data["config"]
    db: BotDB = application.bot_data["db"]
    media_cache: EncryptedMediaCache = application.bot_data["media_cache"]
    media_cache.cleanup()
    deleted = db.prune_feedback(
        unreacted_ttl_seconds=config.feedback_unreacted_ttl_seconds,
        unreacted_max_rows=config.feedback_unreacted_max_rows,
        unreacted_max_bytes=config.feedback_unreacted_max_bytes,
        reacted_max_rows=config.feedback_reacted_max_rows,
        reacted_max_bytes=config.feedback_reacted_max_bytes,
        media_max_bytes=config.feedback_media_max_bytes,
    )
    free_bytes = shutil.disk_usage("/data").free
    if free_bytes < config.disk_free_warning_bytes:
        logger.warning("low bot data disk space free_bytes=%d", free_bytes)
    if any(deleted.values()):
        logger.info("feedback maintenance deleted=%s", deleted)


async def _maintenance_loop(application: Application) -> None:
    config: Config = application.bot_data["config"]
    while True:
        await asyncio.sleep(config.maintenance_interval_seconds)
        await asyncio.to_thread(_run_maintenance, application)


def build_application(config: Config) -> Application:
    builder = (
        ApplicationBuilder()
        .token(config.bot_token)
        .concurrent_updates(8)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
    )
    if config.telegram_proxy_url:
        builder = builder.proxy(config.telegram_proxy_url).get_updates_proxy(
            config.telegram_proxy_url
        )
    application = builder.build()
    application.bot_data["config"] = config
    db = BotDB(
        config.db_path,
        default_recent_context_messages=config.default_recent_context_messages,
        default_rate_limit_user_requests=config.rate_limit_user_requests,
        default_rate_limit_chat_requests=config.rate_limit_chat_requests,
    )
    encryptor = FeedbackEncryptor(config.feedback_public_key_b64)
    application.bot_data["db"] = db
    application.bot_data["businessgpt"] = BusinessGPTClient(
        config.api_key, config.allowed_api_urls
    )
    application.bot_data["feedback_encryptor"] = encryptor
    application.bot_data["context_cache"] = ContextCache(
        max_messages_per_chat=20,
        max_chats=config.context_cache_max_chats,
        ttl_seconds=config.context_cache_ttl_seconds,
    )
    application.bot_data["generation_queue"] = GenerationQueue(
        max_concurrent=config.max_concurrent_generations,
        max_queue_size=config.generation_queue_size,
        user_requests=config.rate_limit_user_requests,
        chat_requests=config.rate_limit_chat_requests,
        window_seconds=config.rate_limit_window_seconds,
    )
    application.bot_data["media_cache"] = EncryptedMediaCache(
        directory=config.media_cache_dir,
        ttl_seconds=config.media_cache_ttl_seconds,
        max_bytes=config.media_cache_max_bytes,
        db=db,
        encryptor=encryptor,
    )

    application.add_handler(TypeHandler(Update, authorize_update), group=-100)
    application.add_handler(MessageHandler(filters.ALL, remember_message), group=-1)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("gpt", gpt_command))
    application.add_handler(CommandHandler("prompts", prompts_command))
    application.add_handler(CommandHandler("prompt_show", prompt_show_command))
    application.add_handler(CommandHandler("prompt_use", prompt_use_command))
    application.add_handler(CommandHandler("prompt_new", prompt_new_or_edit_command))
    application.add_handler(CommandHandler("prompt_edit", prompt_new_or_edit_command))
    application.add_handler(CommandHandler("prompt_delete", prompt_delete_command))
    application.add_handler(CommandHandler("prompt_reset", prompt_reset_command))
    application.add_handler(CommandHandler("prompt_test", prompt_test_command))
    application.add_handler(CommandHandler("models", models_command))
    application.add_handler(CommandHandler("model_show", model_show_command))
    application.add_handler(CommandHandler("model_use", model_use_command))
    application.add_handler(CommandHandler("model_new", model_new_or_edit_command))
    application.add_handler(CommandHandler("model_edit", model_new_or_edit_command))
    application.add_handler(CommandHandler("model_delete", model_delete_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("tools", tools_command))
    application.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^s:"))
    application.add_handler(
        MessageReactionHandler(
            reaction_update,
            message_reaction_types=MessageReactionHandler.MESSAGE_REACTION_UPDATED,
        )
    )
    application.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.IMAGE, media_message)
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = Config.from_env()
    app = build_application(config)
    app.run_polling(
        allowed_updates=ALLOWED_UPDATES,
        drop_pending_updates=False,
        bootstrap_retries=-1,
    )


if __name__ == "__main__":
    main()
