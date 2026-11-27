/** Accept legacy model delimiters for reading only. Code and saved source remain untouched. */
export function normalizeMath(markdown: string): string {
  const prose = (text: string) => {
    const pieces = text.split(/(`+[^`]*?`+)/g);
    return pieces.map((piece, index) => index % 2 ? piece : piece
      // Some real responses put the closing $$ after \end{cases}. Markdown
      // needs a separate closing line, or the rest of the note becomes math.
      .replace(/\$\$([\s\S]*?)\$\$/g, (_, value: string) => `\n$$\n${value.trim()}\n$$\n`)
      .replace(/\\\[([\s\S]*?)\\\]/g, (_, value: string) => `\n$$\n${value.trim()}\n$$\n`)
      .replace(/\\\(([\s\S]*?)\\\)/g, (_, value: string) => `$${value}$`)).join('');
  };
  let fence = ''; let pending = ''; let result = '';
  for (const line of markdown.split(/(?<=\n)/)) {
    const marker = line.match(/^ {0,3}(`{3,}|~{3,})/);
    if (fence) {
      result += line;
      if (marker && marker[1][0] === fence[0] && marker[1].length >= fence.length && /^\s*$/.test(line.slice(line.indexOf(marker[1]) + marker[1].length))) fence = '';
    } else if (marker) { result += prose(pending) + line; pending = ''; fence = marker[1]; }
    else pending += line;
  }
  return result + prose(pending);
}
