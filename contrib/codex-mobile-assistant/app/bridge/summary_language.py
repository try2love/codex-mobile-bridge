"""Display-language preference for future messages submitted from the phone."""

SUMMARY_LANGUAGE_SUFFIX = (
    "\n\n<codex_mobile_display_preferences>\n"
    "请用简体中文撰写对用户可见的思考摘要（若提供），不要使用英文段落；"
    "代码、路径和必要标识符保留原文。不需要展开内部推理过程。\n"
    "</codex_mobile_display_preferences>"
)


def with_summary_language(text):
    # Keep structured question replies intact and avoid duplicating the preference.
    if text.lstrip().startswith("<send_user_message_question_reply>") or text.endswith(SUMMARY_LANGUAGE_SUFFIX):
        return text
    return text + SUMMARY_LANGUAGE_SUFFIX


def strip_summary_language(text):
    return text.removesuffix(SUMMARY_LANGUAGE_SUFFIX)
