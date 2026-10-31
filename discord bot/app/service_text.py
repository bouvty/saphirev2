"""Convert provider-supplied HTML service descriptions into safe Discord text."""
from html.parser import HTMLParser
import re

class _ServiceHTML(HTMLParser):
    _BLOCKS = {'p', 'div', 'section', 'article', 'ul', 'ol', 'table', 'tr', 'h1', 'h2', 'h3', 'h4', 'br'}
    _IGNORE = {'script', 'style', 'iframe', 'svg'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = 0
        self.list_depth = 0
        self.li_count = 0

    def _newline(self):
        if self.parts and not self.parts[-1].endswith('\n'):
            self.parts.append('\n')

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self._IGNORE:
            self.ignored += 1
            return
        if self.ignored:
            return
        if tag in self._BLOCKS:
            self._newline()
        if tag in ('ul', 'ol'):
            self.list_depth += 1
        if tag == 'li':
            self._newline()
            self.parts.append('• ')
        elif tag == 'br':
            self._newline()
        elif tag in ('td', 'th') and self.parts and not self.parts[-1].endswith(('\n', ' ')):
            self.parts.append('  ')

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self._IGNORE:
            self.ignored = max(0, self.ignored - 1)
            return
        if self.ignored:
            return
        if tag in ('ul', 'ol'):
            self.list_depth = max(0, self.list_depth - 1)
        if tag in self._BLOCKS or tag == 'li':
            self._newline()

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def format_service_description(raw, limit=1700):
    """Render provider HTML as readable, neutral plain text without Discord mentions.

    No HTML is executed or interpreted as Discord markdown. Original data is
    retained in the database and formatting occurs only when displaying it.
    """
    if not raw:
        return 'No additional description provided.'
    parser = _ServiceHTML()
    parser.feed(str(raw))
    parser.close()
    text = ''.join(parser.parts)
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r'\s+', ' ', line).strip()
        if cleaned:
            # Prevent provider descriptions from pinging @everyone / roles,
            # interfering with formatting, or using backticks for code blocks.
            cleaned = cleaned.replace('\\', '\\\\').replace('`', '\u02cb')
            cleaned = cleaned.replace('@', '@\u200b')
            cleaned = cleaned.replace('*', '\u2217').replace('_', '\u005f')
            lines.append(cleaned)
    text = '\n'.join(lines).strip()
    if not text:
        return 'No additional description provided.'
    if len(text) > limit:
        text = text[:max(0, limit - 1)].rstrip() + '…'
    return text
