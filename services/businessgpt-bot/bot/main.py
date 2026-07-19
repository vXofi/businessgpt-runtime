from __future__ import annotations

import logging

from telegram import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    MessageReactionHandler,
    filters,
)

from .businessgpt_client import BusinessGPTClient
from .config import Config
from .crypto import FeedbackEncryptor
from .context_cache import ContextCache
from .db import BotDB
from .media_cache import EncryptedMediaCache
from .handlers import (
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
    BotCommand("model_new", "Create a model profile"),
    BotCommand("model_edit", "Edit a model profile"),
    BotCommand("model_delete", "Delete a model profile"),
]

logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    config: Config = application.bot_data["config"]
    db: BotDB = application.bot_data["db"]
    db.migrate()
    db.seed_default_model_profile(
        api_url=config.api_url,
        max_tokens=config.default_max_tokens,
        temperature=config.default_temperature,
        top_p=config.default_top_p,
        top_k=config.default_top_k,
        repetition_penalty=config.default_repetition_penalty,
    )
    application.bot_data["media_cache"].cleanup()
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
    except TelegramError as exc:
        logger.warning("could not register Telegram command menu: %s", exc)
    logger.info(
        "bot initialized username=%s telegram_proxy=%s",
        me.username,
        "configured" if config.telegram_proxy_url else "disabled",
    )


async def post_shutdown(application: Application) -> None:
    api: BusinessGPTClient = application.bot_data["businessgpt"]
    db: BotDB = application.bot_data["db"]
    await api.close()
    db.close()


def build_application(config: Config) -> Application:
    builder = (
        ApplicationBuilder()
        .token(config.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
    )
    if config.telegram_proxy_url:
        builder = builder.proxy(config.telegram_proxy_url).get_updates_proxy(
            config.telegram_proxy_url
        )
    application = builder.build()
    application.bot_data["config"] = config
    db = BotDB(config.db_path)
    encryptor = FeedbackEncryptor(config.feedback_public_key_b64)
    application.bot_data["db"] = db
    application.bot_data["businessgpt"] = BusinessGPTClient(config.api_key)
    application.bot_data["feedback_encryptor"] = encryptor
    application.bot_data["context_cache"] = ContextCache(max_messages_per_chat=20)
    application.bot_data["media_cache"] = EncryptedMediaCache(
        directory=config.media_cache_dir,
        ttl_seconds=config.media_cache_ttl_seconds,
        max_bytes=config.media_cache_max_bytes,
        db=db,
        encryptor=encryptor,
    )

    application.add_handler(MessageHandler(filters.ALL, remember_message), group=-1)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("settings", settings_command))
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
