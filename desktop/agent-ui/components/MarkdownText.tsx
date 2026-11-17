'use client'

import React, { type ReactNode } from 'react'

type Block =
  | { type: 'heading'; level: number; text: string }
  | { type: 'paragraph'; lines: string[] }
  | { type: 'code'; language: string; text: string }
  | { type: 'list'; ordered: boolean; items: { text: string; indent: number }[] }
  | { type: 'quote'; lines: string[] }
  | { type: 'table'; header: string[]; rows: string[][] }
  | { type: 'hr' }

const TABLE_SEPARATOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/
const LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/
const HEADING = /^\s{0,3}(#{1,6})\s+(.*)$/
const HR = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/

function splitTableRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '')
  return trimmed.split('|').map((cell) => cell.trim())
}

function isTableStart(lines: string[], index: number): boolean {
  if (index + 1 >= lines.length) return false
  return lines[index].includes('|') &&
    lines[index + 1].includes('|') &&
    TABLE_SEPARATOR.test(lines[index + 1])
}

function parseBlocks(source: string): Block[] {
  const lines = String(source || '').replace(/\r\n?/g, '\n').split('\n')
  const blocks: Block[] = []
  let index = 0
  while (index < lines.length) {
    const line = lines[index]
    if (!line.trim()) { index += 1; continue }

    const fence = line.match(/^\s*```\s*([\w+-]*)\s*$/)
    if (fence) {
      const language = fence[1] || ''
      const body: string[] = []
      index += 1
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
        body.push(lines[index]); index += 1
      }
      if (index < lines.length) index += 1
      blocks.push({ type: 'code', language, text: body.join('\n') })
      continue
    }

    const heading = line.match(HEADING)
    if (heading) {
      blocks.push({ type: 'heading', level: heading[1].length, text: heading[2].trim() })
      index += 1
      continue
    }

    if (HR.test(line)) { blocks.push({ type: 'hr' }); index += 1; continue }

    if (/^\s*>\s?/.test(line)) {
      const quote: string[] = []
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        quote.push(lines[index].replace(/^\s*>\s?/, '')); index += 1
      }
      blocks.push({ type: 'quote', lines: quote })
      continue
    }

    if (isTableStart(lines, index)) {
      const header = splitTableRow(lines[index])
      index += 2
      const rows: string[][] = []
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        rows.push(splitTableRow(lines[index])); index += 1
      }
      blocks.push({ type: 'table', header, rows })
      continue
    }

    const listMatch = line.match(LIST_ITEM)
    if (listMatch) {
      const ordered = /\d/.test(listMatch[2])
      const items: { text: string; indent: number }[] = []
      while (index < lines.length) {
        const item = lines[index].match(LIST_ITEM)
        if (!item) break
        if (/\d/.test(item[2]) !== ordered) break
        items.push({ text: item[3].trim(), indent: Math.min(4, Math.floor(item[1].length / 2)) })
        index += 1
      }
      blocks.push({ type: 'list', ordered, items })
      continue
    }

    const paragraph: string[] = []
    while (index < lines.length && lines[index].trim()) {
      const current = lines[index]
      if (paragraph.length && (
        HEADING.test(current) || /^\s*```/.test(current) || LIST_ITEM.test(current) ||
        /^\s*>\s?/.test(current) || HR.test(current) || isTableStart(lines, index)
      )) break
      paragraph.push(current.trimEnd()); index += 1
    }
    blocks.push({ type: 'paragraph', lines: paragraph })
  }
  return blocks
}

function safeHref(raw: string): string | null {
  const value = raw.trim()
  if (/^https?:\/\//i.test(value) || /^mailto:/i.test(value)) return value
  return null
}

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const pattern = /(\*\*[^*]+\*\*|__[^_]+__|~~[^~]+~~|`[^`\n]+`|\*[^*\n]+\*|_[^_\n]+_|\[[^\]]+\]\([^)\s]+\)|https?:\/\/[^\s<>()]+)/g
  const nodes: ReactNode[] = []
  let cursor = 0
  let match: RegExpExecArray | null
  let key = 0
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index))
    const token = match[0]
    const id = `${keyPrefix}-${key++}`
    if (token.startsWith('**') || token.startsWith('__')) {
      nodes.push(<strong key={id}>{renderInline(token.slice(2, -2), id)}</strong>)
    } else if (token.startsWith('~~')) {
      nodes.push(<del key={id}>{renderInline(token.slice(2, -2), id)}</del>)
    } else if (token.startsWith('`')) {
      nodes.push(<code key={id} className="md-inline-code">{token.slice(1, -1)}</code>)
    } else if (token.startsWith('*') || token.startsWith('_')) {
      nodes.push(<em key={id}>{renderInline(token.slice(1, -1), id)}</em>)
    } else if (token.startsWith('[')) {
      const link = token.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/)
      const href = link ? safeHref(link[2]) : null
      if (link && href) {
        nodes.push(<a key={id} href={href} target="_blank" rel="noreferrer noopener">{link[1]}</a>)
      } else {
        nodes.push(token)
      }
    } else {
      const href = safeHref(token)
      if (href) nodes.push(<a key={id} href={href} target="_blank" rel="noreferrer noopener">{token}</a>)
      else nodes.push(token)
    }
    cursor = match.index + token.length
  }
  if (cursor < text.length) nodes.push(text.slice(cursor))
  return nodes
}

function paragraphNode(lines: string[], key: string): ReactNode {
  return (
    <p key={key}>
      {lines.map((line, index) => (
        <React.Fragment key={`${key}-${index}`}>
          {index > 0 ? <br /> : null}
          {renderInline(line, `${key}-${index}`)}
        </React.Fragment>
      ))}
    </p>
  )
}

export function MarkdownText({ text, className = '' }: { text: string; className?: string }) {
  const blocks = parseBlocks(text)
  return (
    <div className={`message-text markdown ${className}`.trim()}>
      {blocks.map((block, index) => {
        const key = `md-${index}`
        switch (block.type) {
          case 'heading': {
            const Tag = `h${block.level}` as keyof React.JSX.IntrinsicElements
            return <Tag key={key}>{renderInline(block.text, key)}</Tag>
          }
          case 'paragraph':
            return paragraphNode(block.lines, key)
          case 'code':
            return (
              <pre key={key} className="md-code-block">
                <code>{block.text}</code>
              </pre>
            )
          case 'list':
            return block.ordered ? (
              <ol key={key}>{block.items.map((item, i) => (
                <li key={`${key}-${i}`} style={{ marginLeft: `${item.indent * 16}px` }}>
                  {renderInline(item.text, `${key}-${i}`)}
                </li>
              ))}</ol>
            ) : (
              <ul key={key}>{block.items.map((item, i) => (
                <li key={`${key}-${i}`} style={{ marginLeft: `${item.indent * 16}px` }}>
                  {renderInline(item.text, `${key}-${i}`)}
                </li>
              ))}</ul>
            )
          case 'quote':
            return (
              <blockquote key={key}>
                {block.lines.map((line, i) => (
                  <React.Fragment key={`${key}-${i}`}>
                    {i > 0 ? <br /> : null}
                    {renderInline(line, `${key}-${i}`)}
                  </React.Fragment>
                ))}
              </blockquote>
            )
          case 'table':
            return (
              <div className="md-table-wrap" key={key}>
                <table>
                  <thead><tr>{block.header.map((cell, i) => (
                    <th key={`${key}-h-${i}`}>{renderInline(cell, `${key}-h-${i}`)}</th>
                  ))}</tr></thead>
                  <tbody>{block.rows.map((row, r) => (
                    <tr key={`${key}-r-${r}`}>{block.header.map((_, c) => (
                      <td key={`${key}-r-${r}-${c}`}>{renderInline(row[c] || '', `${key}-r-${r}-${c}`)}</td>
                    ))}</tr>
                  ))}</tbody>
                </table>
              </div>
            )
          case 'hr':
            return <hr key={key} />
          default:
            return null
        }
      })}
    </div>
  )
}