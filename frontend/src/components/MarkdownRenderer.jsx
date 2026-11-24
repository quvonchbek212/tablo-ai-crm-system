import React from 'react';

/**
 * Enterprise MarkdownRenderer for executive AI chat feed.
 * Formats markdown headers, bold cyan highlights, bullet points, numbered lists,
 * code blocks, and typewriter streaming cursor cleanly without external heavyweight dependencies.
 */
export default function MarkdownRenderer({ content = '', isStreaming = false }) {
  if (!content) {
    return isStreaming ? (
      <span className="inline-block w-2 h-4 bg-cyan-400 animate-pulse ml-0.5 align-middle" />
    ) : null;
  }

  // Split lines
  const lines = String(content).split('\n');

  const renderInline = (text) => {
    if (!text) return null;

    // Tokens: bold (**...**), code (`...`), italic (*...*)
    const parts = [];
    let remaining = text;
    let keyIdx = 0;

    // Combined regex for bold (**...**) and inline code (`...`)
    const regex = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g;
    let match;
    let lastIndex = 0;

    while ((match = regex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        parts.push(text.slice(lastIndex, match.index));
      }

      const matchText = match[0];
      if (matchText.startsWith('**') && matchText.endsWith('**')) {
        parts.push(
          <strong key={keyIdx++} className="font-extrabold text-cyan-300">
            {matchText.slice(2, -2)}
          </strong>
        );
      } else if (matchText.startsWith('`') && matchText.endsWith('`')) {
        parts.push(
          <code key={keyIdx++} className="px-1.5 py-0.5 rounded-md bg-[#162035] text-blue-300 font-mono text-[11px] border border-blue-500/20">
            {matchText.slice(1, -1)}
          </code>
        );
      } else if (matchText.startsWith('*') && matchText.endsWith('*')) {
        parts.push(
          <em key={keyIdx++} className="text-slate-300 italic">
            {matchText.slice(1, -1)}
          </em>
        );
      }

      lastIndex = regex.lastIndex;
    }

    if (lastIndex < text.length) {
      parts.push(text.slice(lastIndex));
    }

    return parts;
  };

  const renderedBlocks = [];
  let currentList = [];
  let isNumberList = false;

  const flushList = () => {
    if (currentList.length > 0) {
      if (isNumberList) {
        renderedBlocks.push(
          <ol key={`ol-${renderedBlocks.length}`} className="space-y-1.5 my-2.5 pl-2">
            {currentList.map((item, idx) => (
              <li key={idx} className="flex items-start space-x-2.5 text-slate-200 text-xs leading-relaxed">
                <span className="shrink-0 w-5 h-5 rounded-full bg-blue-500/10 border border-blue-500/20 text-blue-400 font-mono text-[10px] font-bold flex items-center justify-center mt-0.5">
                  {idx + 1}
                </span>
                <span className="flex-1">{renderInline(item)}</span>
              </li>
            ))}
          </ol>
        );
      } else {
        renderedBlocks.push(
          <ul key={`ul-${renderedBlocks.length}`} className="space-y-1.5 my-2.5 pl-2">
            {currentList.map((item, idx) => (
              <li key={idx} className="flex items-start space-x-2.5 text-slate-200 text-xs leading-relaxed">
                <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-cyan-400 mt-2 shadow-sm shadow-cyan-400/50" />
                <span className="flex-1">{renderInline(item)}</span>
              </li>
            ))}
          </ul>
        );
      }
      currentList = [];
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const rawLine = lines[i];
    const trimmed = rawLine.trim();

    if (!trimmed) {
      flushList();
      continue;
    }

    // Header 3: ###
    if (trimmed.startsWith('### ')) {
      flushList();
      renderedBlocks.push(
        <h4 key={`h3-${i}`} className="text-sm font-bold text-white mt-4 mb-2 flex items-center space-x-2">
          <span className="w-1.5 h-4 rounded-full bg-gradient-to-b from-blue-500 to-cyan-400 shrink-0" />
          <span>{renderInline(trimmed.slice(4))}</span>
        </h4>
      );
      continue;
    }

    // Header 2: ##
    if (trimmed.startsWith('## ')) {
      flushList();
      renderedBlocks.push(
        <h3 key={`h2-${i}`} className="text-base font-extrabold text-white mt-5 mb-2.5 flex items-center space-x-2">
          <span className="w-2 h-4.5 rounded-full bg-gradient-to-b from-blue-500 to-cyan-400 shrink-0" />
          <span>{renderInline(trimmed.slice(3))}</span>
        </h3>
      );
      continue;
    }

    // Header 1: #
    if (trimmed.startsWith('# ')) {
      flushList();
      renderedBlocks.push(
        <h2 key={`h1-${i}`} className="text-lg font-black text-white mt-5 mb-3 flex items-center space-x-2">
          <span className="w-2 h-5 rounded-full bg-gradient-to-b from-blue-500 to-cyan-400 shrink-0" />
          <span>{renderInline(trimmed.slice(2))}</span>
        </h2>
      );
      continue;
    }

    // Bullet points: - or *
    if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
      if (currentList.length > 0 && isNumberList) {
        flushList();
      }
      isNumberList = false;
      currentList.push(trimmed.slice(2));
      continue;
    }

    // Numbered list: 1. 2. etc.
    const numMatch = trimmed.match(/^(\d+)\.\s+(.+)$/);
    if (numMatch) {
      if (currentList.length > 0 && !isNumberList) {
        flushList();
      }
      isNumberList = true;
      currentList.push(numMatch[2]);
      continue;
    }

    // Quotes: >
    if (trimmed.startsWith('> ')) {
      flushList();
      renderedBlocks.push(
        <blockquote key={`quote-${i}`} className="my-2.5 pl-3 py-1.5 border-l-2 border-blue-400 bg-blue-500/5 text-slate-300 italic text-xs rounded-r-xl">
          {renderInline(trimmed.slice(2))}
        </blockquote>
      );
      continue;
    }

    // Regular paragraph
    flushList();
    renderedBlocks.push(
      <p key={`p-${i}`} className="text-xs text-slate-200 leading-relaxed my-1.5 font-normal">
        {renderInline(trimmed)}
      </p>
    );
  }

  flushList();

  return (
    <div className="space-y-1 text-slate-200">
      {renderedBlocks}
      {isStreaming && (
        <span className="inline-block w-2 h-4 bg-cyan-400 animate-pulse ml-1 align-middle shadow-lg shadow-cyan-400/50" />
      )}
    </div>
  );
}
