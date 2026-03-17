import regex
import html as html_module

# Thinking block format definitions: (start_tag, end_tag, content_start_tag)
# Use None for start_tag to match from beginning (end-only formats should be listed last)
THINKING_FORMATS = [
    ('<think>', '</think>', None),
    ('<|channel|>analysis<|message|>', '<|end|>', '<|channel|>final<|message|>'),
    ('<|channel|>commentary<|message|>', '<|end|>', '<|channel|>final<|message|>'),
    ('<seed:think>', '</seed:think>', None),
    ('<|think|>', '<|end|>', '<|content|>'),  # Solar Open
    # ('Thinking Process:', '</think>', None),  # Qwen3.5 verbose thinking outside tags -- removed: too prone to false positives in streaming
    (None, '</think>', None),  # End-only variant (e.g., Qwen3-next)
]


def extract_reasoning(text, html_escaped=False):
    """Extract reasoning/thinking blocks from the beginning of a string.

    When html_escaped=True, tags are HTML-escaped before searching
    (for use on already-escaped UI strings).

    Returns (reasoning_content, final_content) where reasoning_content is
    None if no thinking block is found.
    """
    if not text:
        return None, text

    thinking_contents = []
    remaining_contents = []

    esc = html_module.escape if html_escaped else lambda s: s

    content_pos = -1
    for start_tag, end_tag, content_tag in THINKING_FORMATS:
        end_esc = esc(end_tag)
        content_esc = esc(content_tag) if content_tag else None

        if start_tag is None:
            # End-only format: require end tag, start from beginning
            end_pos = text.find(end_esc)
            if end_pos == -1:
                continue
            thinking_contents.append(text[:end_pos])
            remaining_contents.append(text[end_pos + len(end_esc):])
            return thinking_contents, remaining_contents

        # Normal format: require start tag
        start_esc = regex.escape(esc(start_tag))
        start_pos_matches = list(regex.finditer(start_esc, text))
        end_pos = -1
        content_pos = -1
        if not start_pos_matches:
            # During streaming, the start tag may be arriving partially.
            # If the text is a prefix of a start tag, return empty content
            # to prevent the partial tag from leaking.
            stripped = text.strip()
            if stripped and start_esc.startswith(stripped):
                return [], []
            continue
        for start_pos_match in start_pos_matches:
            tag_start, tag_end = start_pos_match.span()
            content_start = tag_end
            if end_pos > tag_start or content_pos > tag_start:
                continue
            if end_pos > 0:
                remaining_contents.append(text[end_pos + len(end_esc):tag_start])

            end_pos = text.find(end_esc, content_start)
            if end_pos == -1:
                # End tag missing - check if content tag can serve as fallback
                if content_esc and (content_pos := text.find(content_esc, content_start)) != -1:
                    thinking_content = text[content_start:content_pos]
                    gpt_oss_marker = esc("<|start|>assistant")
                    if thinking_content.endswith(gpt_oss_marker):
                        thinking_content = thinking_content[:-len(gpt_oss_marker)]
                    thinking_contents.append(thinking_content)
                else:
                    # Only opening tag found - everything else is thinking content
                    thinking_contents.append(text[content_start:])
            else:
                # Both tags found - extract content between them
                thinking_contents.append(text[content_start:end_pos])

        if thinking_contents:
            if content_pos != -1:
                remaining_content = text[content_pos + len(content_esc):]
            elif end_pos == -1:
                remaining_content = ""
            else:
                remaining_content = text[end_pos + len(end_esc):]
            for marker in ['<|start|>assistant<|channel|>final<|message|>', '<|channel|>final<|message|>']:
                marker_esc = esc(marker)
                if remaining_content.startswith(marker_esc):
                    remaining_content = remaining_content[len(marker_esc):]
            remaining_contents.append(remaining_content)
            return thinking_contents, remaining_contents

    # Handle standalone GPT-OSS final channel marker without a preceding
    # analysis/commentary block (the model skipped thinking entirely).
    for marker in ['<|start|>assistant<|channel|>final<|message|>', '<|channel|>final<|message|>']:
        marker_esc = esc(marker)
        pos = text.find(marker_esc)
        if pos != -1:
            before = text[:pos].strip()
            after = text[pos + len(marker_esc):]
            return ([before] if before else []), [after]

    return [], [text]