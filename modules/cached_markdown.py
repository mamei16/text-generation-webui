# Python Markdown

# A Python implementation of John Gruber's Markdown.

# Documentation: https://python-markdown.github.io/
# GitHub: https://github.com/Python-Markdown/markdown/
# PyPI: https://pypi.org/project/Markdown/

# Started by Manfred Stienstra (http://www.dwerg.net/).
# Maintained for a few years by Yuri Takhteyev (http://www.freewisdom.org).
# Currently maintained by Waylan Limberg (https://github.com/waylan),
# Dmitry Shachnev (https://github.com/mitya57) and Isaac Muse (https://github.com/facelessuser).

# Copyright 2007-2023 The Python Markdown Project (v. 1.7 and later)
# Copyright 2004, 2005, 2006 Yuri Takhteyev (v. 0.2-1.6b)
# Copyright 2004 Manfred Stienstra (the original version)

# License: BSD (see LICENSE.md for details).

from __future__ import annotations

import logging
from typing import TYPE_CHECKING,  Callable, ClassVar

import regex as re
from markdown import util

from markdown.inlinepatterns import DelimiterProcessor
from markdown.extensions import Extension
from markdown.serializers import to_html_string, to_xhtml_string
from markdown.util import BLOCK_LEVEL_ELEMENTS
from markdown import Markdown

if TYPE_CHECKING:  # pragma: no cover
    from xml.etree.ElementTree import Element


logger = logging.getLogger('MARKDOWN')


class CachedMarkdown(Markdown):
    """
    A parser which converts Markdown to HTML.

    Attributes:
        Markdown.tab_length (int): The number of spaces which correspond to a single tab. Default: `4`.
        Markdown.ESCAPED_CHARS (list[str]): List of characters which get the backslash escape treatment.
        Markdown.block_level_elements (list[str]): List of HTML tags which get treated as block-level elements.
            See [`markdown.util.BLOCK_LEVEL_ELEMENTS`][] for the full list of elements.
        Markdown.registeredExtensions (list[Extension]): List of extensions which have called
            [`registerExtension`][markdown.Markdown.registerExtension] during setup.
        Markdown.doc_tag (str): Element used to wrap document. Default: `div`.
        Markdown.stripTopLevelTags (bool): Indicates whether the `doc_tag` should be removed. Default: 'True'.
        Markdown.references (dict[str, tuple[str, str]]): A mapping of link references found in a parsed document
             where the key is the reference name and the value is a tuple of the URL and title.
        Markdown.htmlStash (util.HtmlStash): The instance of the `HtmlStash` used by an instance of this class.
        Markdown.output_formats (dict[str, Callable[xml.etree.ElementTree.Element]]): A mapping of known output
             formats by name and their respective serializers. Each serializer must be a callable which accepts an
            [`Element`][xml.etree.ElementTree.Element] and returns a `str`.
        Markdown.output_format (str): The output format set by
            [`set_output_format`][markdown.Markdown.set_output_format].
        Markdown.serializer (Callable[xml.etree.ElementTree.Element]): The serializer set by
            [`set_output_format`][markdown.Markdown.set_output_format].
        Markdown.preprocessors (util.Registry): A collection of [`preprocessors`][markdown.preprocessors].
        Markdown.parser (blockparser.BlockParser): A collection of [`blockprocessors`][markdown.blockprocessors].
        Markdown.inlinePatterns (util.Registry): A collection of [`inlinepatterns`][markdown.inlinepatterns].
        Markdown.treeprocessors (util.Registry): A collection of [`treeprocessors`][markdown.treeprocessors].
        Markdown.postprocessors (util.Registry): A collection of [`postprocessors`][markdown.postprocessors].

    """

    doc_tag = "div"     # Element used to wrap document - later removed

    output_formats: ClassVar[dict[str, Callable[[Element], str]]] = {
        'html':   to_html_string,
        'xhtml':  to_xhtml_string,
    }
    """
    A mapping of known output formats by name and their respective serializers. Each serializer must be a
    callable which accepts an [`Element`][xml.etree.ElementTree.Element] and returns a `str`.
    """

    def __init__(self, **kwargs):
        """
        Creates a new Markdown instance.

        Keyword Arguments:
            extensions (list[Extension | str]): A list of extensions.

                If an item is an instance of a subclass of [`markdown.extensions.Extension`][],
                the instance will be used as-is. If an item is of type `str`, it is passed
                to [`build_extension`][markdown.Markdown.build_extension] with its corresponding
                `extension_configs` and the returned instance  of [`markdown.extensions.Extension`][]
                is used.
            extension_configs (dict[str, dict[str, Any]]): Configuration settings for extensions.
            output_format (str): Format of output. Supported formats are:

                * `xhtml`: Outputs XHTML style tags. Default.
                * `html`: Outputs HTML style tags.
            tab_length (int): Length of tabs in the source. Default: `4`

        """

        self.last_run: float = 0.0
        self.tab_length: int = kwargs.get('tab_length', 4)

        self.ESCAPED_CHARS: list[str] = [
            '\\', '`', '*', '_', '{', '}', '[', ']', '(', ')', '>', '#', '+', '-', '.', '!'
        ]
        """ List of characters which get the backslash escape treatment. """

        self.block_level_elements: list[str] = BLOCK_LEVEL_ELEMENTS.copy()

        self.registeredExtensions: list[Extension] = []
        self.docType = ""  # TODO: Maybe delete this. It does not appear to be used anymore.
        self.stripTopLevelTags: bool = True
        self.delimiters: DelimiterProcessor | None = None

        # Incremental (streaming) cache used by :meth:`convert`.  ``convert``
        # is typically called repeatedly with text that grows only by
        # appending tokens (LLM streaming).  Blocks that are already
        # complete cannot change when text is appended at the end, so their
        # rendered HTML is cached here and reused across calls.
        self.stream_cache_lines: list[str] | None = None
        self.stream_cache_html: str = ''

        self.build_parser()

        self.references: dict[str, tuple[str, str]] = {}
        self.htmlStash: util.HtmlStash = util.HtmlStash()
        self._ext_input = kwargs.get('extensions', [])
        self._ext_configs = kwargs.get('extension_configs', {})
        self._output_format = kwargs.get('output_format', 'xhtml')
        self.registerExtensions(extensions=self._ext_input, configs=self._ext_configs)
        self.set_output_format(kwargs.get('output_format', 'xhtml'))
        self.reset()

    def convert(self, source: str) -> str:
        """
        Convert a Markdown string to a string in the specified output format.

        Arguments:
            source: Markdown formatted text as Unicode or ASCII string.

        Returns:
            A string in the specified output format.

        Markdown parsing takes place in five steps:

        1. A bunch of [`preprocessors`][markdown.preprocessors] munge the input text.
        2. A [`BlockParser`][markdown.blockparser.BlockParser] parses the high-level structural elements of the
           pre-processed text into an [`ElementTree`][xml.etree.ElementTree.ElementTree] object.
        3. A bunch of [`treeprocessors`][markdown.treeprocessors] are run against the
           [`ElementTree`][xml.etree.ElementTree.ElementTree] object. One such `treeprocessor`
           ([`markdown.treeprocessors.InlineProcessor`][]) runs [`inlinepatterns`][markdown.inlinepatterns]
           against the [`ElementTree`][xml.etree.ElementTree.ElementTree] object, parsing inline markup.
        4. Some [`postprocessors`][markdown.postprocessors] are run against the text after the
           [`ElementTree`][xml.etree.ElementTree.ElementTree] object has been serialized into text.
        5. The output is returned as a string.

        !!! warning
            The Python-Markdown library does ***not*** sanitize its HTML output.
            If you are processing Markdown input from an untrusted source, it is your
            responsibility to ensure that it is properly sanitized. For more
            information see [Sanitizing HTML Output](../../sanitization.md).

        This method is optimized for incremental (streaming) rendering.  When it is
        called repeatedly with text that only grows by appending new content at the
        end -- as happens while streaming tokens from an LLM (possibly throttled so
        several tokens are added at once) -- the blocks that are already complete are
        reused from an internal cache instead of being re-parsed, and only the
        trailing, still-growing block is converted again.  Because Markdown blocks
        are separated at block boundaries, the cached HTML and the freshly rendered
        tail simply concatenate to the same result a full conversion would produce.

        The cache is invalidated automatically whenever the incoming text no longer
        extends the previously seen text, so converting unrelated documents remains
        correct.

        """

        # Fix up the source text
        if not source.strip():
            return ''  # a blank Unicode string

        try:
            source = str(source)
        except UnicodeDecodeError as e:  # pragma: no cover
            # Customize error message while maintaining original traceback
            e.reason += '. -- Note: Markdown only accepts Unicode input!'
            raise

        # Caching relies on stripping the wrapping document tag so that the HTML of
        # consecutive block groups can be concatenated.  Keep the tags -> plain full
        # conversion.
        if not self.stripTopLevelTags:
            return self._convert_normal(source)

        lines = source.split("\n")
        boundary = self._complete_prefix_length(lines)

        cache = self.stream_cache_lines
        if cache is not None and len(lines) >= len(cache) and lines[:len(cache)] == cache and boundary >= len(cache):
            # The source only grew since the last call.  Render any blocks that have
            # become complete since then, cache them, and render the still-growing tail.
            cached_html = self.stream_cache_html
            newly_complete = lines[len(cache):boundary]
            if newly_complete and any(line.strip() for line in newly_complete):
                cached_html += self._render(lines[len(cache):boundary])
            tail_html = self._render(lines[boundary:])

            self.stream_cache_html = cached_html
            self.stream_cache_lines = lines[:boundary]
            return (cached_html + tail_html).strip()

        # No usable cache (first call, or the text no longer extends the cache):
        # do a plain full conversion so global state (references, footnotes, ...) is
        # preserved exactly and extension state accumulates the same way it would
        # without reset.  Then seed the cache with the already-complete prefix.
        # The prefix is rendered on an independent clone so that this instance's
        # state (e.g. ``Markdown.references`` and registered extension state) is left
        # untouched.  Nothing is cached when the whole document is a single growing
        # block, so unrelated documents are never mistaken for a streaming extension.
        result = self._convert_normal(source)
        prefix = lines[:boundary]
        if prefix:
            self.stream_cache_html = self._clone()._render(prefix)
            self.stream_cache_lines = prefix
        else:
            self.stream_cache_html = ''
            self.stream_cache_lines = None
        return result

    def _render(self, lines: list[str]) -> str:
        """
        Run the full Markdown pipeline over ``lines`` and return the serialized HTML.

        The document wrapper is omitted (only the block-level children are emitted)
        so that the result of separate calls can be concatenated verbatim: the block
        HTML is independent across block boundaries and the placeholders left by the
        preprocessors are resolved here, so concatenating block groups reproduces a
        single full render exactly.
        """
        if not any(line.strip() for line in lines):
            return ''

        self.reset()

        self.lines = lines
        for prep in self.preprocessors:
            self.lines = prep.run(self.lines)

        root = self.parser.parseDocument(self.lines).getroot()

        for treeprocessor in self.treeprocessors:
            newRoot = treeprocessor.run(root)
            if newRoot is not None:
                root = newRoot

        output = ''.join(self.serializer(child) for child in root)

        for pp in self.postprocessors:
            output = pp.run(output)

        return output

    def _clone(self) -> CachedMarkdown:
        """ A fresh instance with the same extensions and settings but independent state. """
        return CachedMarkdown(
            extensions=self._ext_input,
            extension_configs=self._ext_configs,
            output_format=self._output_format,
            tab_length=self.tab_length,
        )

    def _complete_prefix_length(self, lines: list[str]) -> int:
        """
        Return the number of leading *raw* source lines that are already complete.

        Everything up to the returned index consists of finished blocks that cannot
        change when text is appended at the end; the remainder is the single trailing
        block that may still grow.  The index is always a block boundary, so the
        prefix and the tail can be rendered independently and concatenated.

        Scanning is done on the raw lines (tabs expanded, which does not alter the
        line count) rather than on the pre-processed lines, because preprocessors
        such as the fenced-code block collapse several lines into one placeholder and
        would otherwise break the mapping back onto the source.
        """
        expanded = [line.expandtabs(self.tab_length) for line in lines]

        blocks = self._scan_blocks(expanded)
        if not blocks:
            return 0

        # The last block is the only one that can still grow.
        boundary = blocks[-1][0]

        # A trailing list, blockquote or indented code block can absorb a block that
        # arrives later (a new list item, quote line or indented block), so it must
        # not be cached.  Move the boundary back to where such a trailing block began,
        # leaving it in the tail that gets re-rendered on the next call.
        while len(blocks) > 1 and self._block_is_mergeable(blocks[-2]):
            boundary = blocks[-2][0]
            blocks.pop()

        return boundary

    def _scan_blocks(self, lines: list[str]) -> list[tuple[int, str]]:
        """
        Split ``lines`` into top-level blocks, returning ``(start_line, kind)`` for
        each.  Blocks normally end at a blank line, but lists, blockquotes and open
        fenced code blocks continue across blank lines, mirroring the block parser.
        """
        tl = self.tab_length
        fence_re = re.compile(r'^([~`]{3,})')
        fence_close_re = re.compile(r'^([~`]{3,})[ ]*$')
        ul_re = re.compile(r'^[ ]{0,%d}[*+-][ ]+' % (tl - 1))
        ol_re = re.compile(r'^[ ]{0,%d}[\*_]{0,2}\d+\.[ ]+' % (tl - 1))
        quote_re = re.compile(r'^[ ]{0,3}>')
        hr_re = re.compile(r'^[ ]{0,3}([-*_])[ ]*(\1[ ]*){2,}[ ]*$')

        def is_blank(line: str) -> bool:
            return not line.strip()

        def is_list_item(line: str) -> bool:
            return bool(ul_re.match(line) or ol_re.match(line))

        def classify(line: str) -> str:
            if fence_re.match(line):
                return 'fence'
            if is_list_item(line):
                return 'list'
            if quote_re.match(line):
                return 'quote'
            if hr_re.match(line):
                return 'hr'
            if line.startswith(' ' * tl):
                return 'code'
            if line.startswith('#'):
                return 'heading'
            return 'paragraph'

        blocks: list[tuple[int, str]] = []
        i = 0
        n = len(lines)
        while i < n:
            if is_blank(lines[i]):
                i += 1
                continue
            start = i
            kind = classify(lines[i])
            fence_marker = None
            if kind == 'fence':
                fence_marker = fence_re.match(lines[i]).group(1)

            # Consume the block: lines up to the next blank line, plus any
            # blank-line-separated chunks that continue this block.
            closed = kind != 'fence'
            while i < n:
                # Read the current chunk (consecutive non-blank lines).
                while i < n and not is_blank(lines[i]):
                    if kind == 'fence' and not closed and fence_close_re.match(lines[i]):
                        if i > start and lines[i][0] == fence_marker[0] and len(fence_close_re.match(lines[i]).group(1)) >= len(fence_marker):
                            closed = True
                    i += 1
                if i < n and is_blank(lines[i]):
                    # Skip the blank line(s) that separate chunks.
                    while i < n and is_blank(lines[i]):
                        i += 1
                    if i >= n:
                        break
                    nxt = lines[i]
                    if kind == 'fence' and not closed:
                        # An open fence swallows everything until it is closed.
                        continue
                    if kind == 'list' and (is_list_item(nxt) or nxt != nxt.lstrip()):
                        # Nested content or another item continues the list.
                        continue
                    if kind == 'quote' and quote_re.match(nxt):
                        # Another quoted line continues the blockquote.
                        continue
                    if kind == 'code' and nxt != nxt.lstrip():
                        # More indented lines continue the (indented) code block.
                        continue
                    break  # a genuinely new block starts here
                else:
                    break

            blocks.append((start, kind))

        return blocks

    def _block_is_mergeable(self, block: tuple[int, str]) -> bool:
        """ Whether a block can absorb a block that follows it across a blank line. """
        return block[1] in ('list', 'quote', 'code')

    def _convert_normal(self, source: str) -> str:
        """
        Convert a Markdown string to a string in the specified output format.

        Arguments:
            source: Markdown formatted text as Unicode or ASCII string.

        Returns:
            A string in the specified output format.

        Markdown parsing takes place in five steps:

        1. A bunch of [`preprocessors`][markdown.preprocessors] munge the input text.
        2. A [`BlockParser`][markdown.blockparser.BlockParser] parses the high-level structural elements of the
           pre-processed text into an [`ElementTree`][xml.etree.ElementTree.ElementTree] object.
        3. A bunch of [`treeprocessors`][markdown.treeprocessors] are run against the
           [`ElementTree`][xml.etree.ElementTree.ElementTree] object. One such `treeprocessor`
           ([`markdown.treeprocessors.InlineProcessor`][]) runs [`inlinepatterns`][markdown.inlinepatterns]
           against the [`ElementTree`][xml.etree.ElementTree.ElementTree] object, parsing inline markup.
        4. Some [`postprocessors`][markdown.postprocessors] are run against the text after the
           [`ElementTree`][xml.etree.ElementTree.ElementTree] object has been serialized into text.
        5. The output is returned as a string.

        !!! warning
            The Python-Markdown library does ***not*** sanitize its HTML output.
            If you are processing Markdown input from an untrusted source, it is your
            responsibility to ensure that it is properly sanitized. For more
            information see [Sanitizing HTML Output](../../sanitization.md).

        """

        # Fix up the source text
        if not source.strip():
            return ''  # a blank Unicode string

        try:
            source = str(source)
        except UnicodeDecodeError as e:  # pragma: no cover
            # Customize error message while maintaining original traceback
            e.reason += '. -- Note: Markdown only accepts Unicode input!'
            raise

        # Split into lines and run the line preprocessors.
        self.lines = source.split("\n")
        for prep in self.preprocessors:
            self.lines = prep.run(self.lines)

        # Parse the high-level elements.
        root = self.parser.parseDocument(self.lines).getroot()

        # Run the tree-processors
        for treeprocessor in self.treeprocessors:
            newRoot = treeprocessor.run(root)
            if newRoot is not None:
                root = newRoot

        # Serialize _properly_.  Strip top-level tags.
        output = self.serializer(root)
        if self.stripTopLevelTags:
            try:
                start = output.index(
                    '<%s>' % self.doc_tag) + len(self.doc_tag) + 2
                end = output.rindex('</%s>' % self.doc_tag)
                output = output[start:end].strip()
            except ValueError as e:  # pragma: no cover
                if output.strip().endswith('<%s />' % self.doc_tag):
                    # We have an empty document
                    output = ''
                else:
                    # We have a serious problem
                    raise ValueError('Markdown failed to strip top-level '
                                     'tags. Document=%r' % output.strip()) from e

        # Run the text post-processors
        for pp in self.postprocessors:
            output = pp.run(output)

        return output.strip()
