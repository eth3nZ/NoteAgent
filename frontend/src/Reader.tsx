import { memo, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import rehypeKatex from 'rehype-katex';
import rehypeHighlight from 'rehype-highlight';
import { api } from './api';
import { normalizeMath } from './math';
import 'katex/dist/katex.min.css';

export interface Heading { id: string; text: string; level: number; }
const schema = { ...defaultSchema, attributes: { ...defaultSchema.attributes,
  code: [...(defaultSchema.attributes?.code || []), ['className', /^language-./, 'math-inline', 'math-display']],
} };
export const Reader = memo(function Reader({ markdown, onHeadings }: { markdown: string; onHeadings?: (items: Heading[]) => void }) {
  const article = useRef<HTMLElement>(null);
  useEffect(() => {
    const headings = Array.from(article.current?.querySelectorAll('h2,h3') || []).map((heading, index) => {
      const id = `note-heading-${index}`; heading.id = id;
      return { id, text: heading.textContent || '', level: Number(heading.tagName.slice(1)) };
    });
    onHeadings?.(headings);
  }, [markdown, onHeadings]);
  return <article ref={article} className="prose">
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[rehypeRaw, [rehypeSanitize, schema], [rehypeKatex, { trust: false, throwOnError: false }], rehypeHighlight]}
      components={{
        a: ({ href, children }) => <a href={/^https?:\/\//i.test(href || '') ? href : undefined}
          onClick={event => { event.preventDefault(); if (href && /^https?:\/\//i.test(href)) void api('open_link', { url: href }); }}>{children}</a>,
        img: () => null,
        table: ({ children }) => <div className="table-scroll"><table>{children}</table></div>,
      }}>{normalizeMath(markdown)}</ReactMarkdown>
  </article>;
});
