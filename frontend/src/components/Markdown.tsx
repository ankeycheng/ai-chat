import { memo, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { Check, Copy } from 'lucide-react'
import type { Source } from '../api/types'

function textOf(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  if (node && typeof node === 'object' && 'props' in node) {
    return textOf((node as { props: { children?: ReactNode } }).props.children)
  }
  return ''
}

function CodeBlock({ children, className }: { children?: ReactNode; className?: string }) {
  const [copied, setCopied] = useState(false)
  const lang = /language-(\w+)/.exec(className ?? '')?.[1] ?? ''
  const code = textOf(children)
  return (
    <div className="code-block">
      <div className="code-head">
        <span>{lang || 'code'}</span>
        <button
          className="code-copy"
          onClick={() => {
            void navigator.clipboard?.writeText(code.replace(/\n$/, ''))
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
          }}
        >
          {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre>
        <code className={className}>{children}</code>
      </pre>
    </div>
  )
}

/** Turn [S1] markers into links only when the source really exists (never fabricate citations). */
export function linkCitations(text: string, sources: Source[]): string {
  if (!sources.length) return text
  const byId = new Map(sources.map((s) => [s.id, s]))
  return text.replace(/\[(S\d+)\](?!\()/g, (m, id: string) => {
    const s = byId.get(id)
    return s ? `[${id}](${s.url} "cite:${id}")` : m
  })
}

export const Markdown = memo(function Markdown({ text, sources = [] }: { text: string; sources?: Source[] }) {
  const byId = new Map(sources.map((s) => [s.id, s]))
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          pre: ({ children }) => <>{children}</>,
          code: ({ className, children, ...rest }) => {
            const inline = !className && !String(textOf(children)).includes('\n')
            if (inline) return <code className="inline-code" {...rest}>{children}</code>
            return <CodeBlock className={className}>{children}</CodeBlock>
          },
          a: ({ href, title, children }) => {
            if (title?.startsWith('cite:')) {
              const s = byId.get(title.slice(5))
              return (
                <a className="cite" href={href} target="_blank" rel="noreferrer noopener" title={s?.title ?? href}>
                  {s?.site_name ?? title.slice(5)}
                </a>
              )
            }
            return (
              <a href={href} target="_blank" rel="noreferrer noopener" title={title}>
                {children}
              </a>
            )
          },
          table: ({ children }) => (
            <div className="table-wrap">
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {linkCitations(text, sources)}
      </ReactMarkdown>
    </div>
  )
})
