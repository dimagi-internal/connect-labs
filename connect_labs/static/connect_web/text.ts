/**
 * Question captions with markdown (`caption_markdown`), rendered safely.
 *
 * HQ forms allow a small markdown: bold, italics, links, lists, headings, line breaks. The text
 * is HTML-escaped FIRST, then only these few tags are introduced, and a link is kept only for an
 * http(s) or mailto URL — so a caption can never inject markup or script.
 */

export function escapeHtml(text: string): string {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function inline(escaped: string): string {
  return escaped
    .replace(
      /\[([^\]]+)\]\(((?:https?:\/\/|mailto:)[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer" class="underline">$1</a>',
    )
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^_]+)__/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
    .replace(/(^|[^_\w])_([^_\s][^_]*)_(?!\w)/g, '$1<em>$2</em>');
}

export function markdownToHtml(source: string | null | undefined): string {
  if (!source) return '';
  const lines = escapeHtml(source).split(/\r?\n/);
  const out: string[] = [];
  let list: 'ul' | 'ol' | null = null;
  const closeList = () => {
    if (list) out.push(`</${list}>`);
    list = null;
  };
  for (const line of lines) {
    const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const heading = line.match(/^\s*(#{1,6})\s+(.*)$/);
    if (bullet || numbered) {
      const kind = bullet ? 'ul' : 'ol';
      if (list !== kind) {
        closeList();
        list = kind;
        out.push(
          kind === 'ul'
            ? '<ul class="ml-5 list-disc">'
            : '<ol class="ml-5 list-decimal">',
        );
      }
      out.push(`<li>${inline((bullet || numbered)![1])}</li>`);
      continue;
    }
    closeList();
    if (heading) {
      out.push(`<p class="font-semibold">${inline(heading[2])}</p>`);
    } else if (line.trim() === '') {
      out.push('<br>');
    } else {
      out.push(`<span>${inline(line)}</span><br>`);
    }
  }
  closeList();
  return out.join('').replace(/(<br>)+$/, '');
}
