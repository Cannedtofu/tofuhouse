import React, { useState, useEffect } from 'react';
import { Plus, Trash2, RefreshCw, Sparkles, ExternalLink, ChevronRight, Newspaper, Twitter } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import Markdown from 'react-markdown';
import { cn } from './lib/utils';
import { FeedSource, FeedItem } from './types';
import { summarizeSource, summarizeArticle } from './services/ai';

const DEFAULT_SOURCES: FeedSource[] = [
  { id: '1', name: 'Elon Musk (X)', url: 'https://nitter.net/elonmusk/rss', type: 'x' },
  { id: '2', name: 'Vitalik Buterin (X)', url: 'https://nitter.net/VitalikButerin/rss', type: 'x' },
  { id: '3', name: 'Sam Altman (X)', url: 'https://nitter.net/sama/rss', type: 'x' },
  { id: '4', name: 'OpenAI News', url: 'https://openai.com/news/rss.xml', type: 'rss' },
];

export default function App() {
  const [sources, setSources] = useState<FeedSource[]>(() => {
    const saved = localStorage.getItem('feed_sources');
    return saved ? JSON.parse(saved) : DEFAULT_SOURCES;
  });
  const [selectedSourceIds, setSelectedSourceIds] = useState<Set<string>>(new Set(sources.map(s => s.id)));
  const [newUrl, setNewUrl] = useState('');
  const [items, setItems] = useState<FeedItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedItem, setSelectedItem] = useState<FeedItem | null>(null);
  
  // Date range state (default to last 30 days for better initial visibility)
  const [dateRange, setDateRange] = useState({
    start: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0],
    end: new Date().toISOString().split('T')[0]
  });

  const [batchSummary, setBatchSummary] = useState<string | null>(null);
  const [isBatchSummarizing, setIsBatchSummarizing] = useState(false);

  useEffect(() => {
    localStorage.setItem('feed_sources', JSON.stringify(sources));
    // Sync selected sources if sources change
    setSelectedSourceIds(prev => {
      const next = new Set(prev);
      sources.forEach(s => {
        if (!prev.has(s.id)) next.add(s.id);
      });
      // Remove deleted sources
      const sourceIds = new Set(sources.map(s => s.id));
      next.forEach(id => {
        if (!sourceIds.has(id)) next.delete(id);
      });
      return next;
    });
  }, [sources]);

  useEffect(() => {
    fetchFeeds();
  }, [sources]); // Refetch if sources change (e.g. added new one)

  const fetchFeeds = async () => {
    if (sources.length === 0) return;
    setLoading(true);
    try {
      const allItems: FeedItem[] = [];
      for (const source of sources) {
        try {
          const res = await fetch(`/api/fetch-rss?url=${encodeURIComponent(source.url)}`);
          if (res.ok) {
            const data = await res.json();
            const feedItems = data.items.map((item: any) => {
              const isRepost = item.title?.startsWith("RT by ");
              const isReply = item.title?.startsWith("R to ");
              
              // Nitter and other RSS feeds often put the full content in different fields
              const fullContent = item['content:encoded'] || item.content || item.description || item.summary || item.contentSnippet || "";
              
              return {
                id: item.guid || item.link,
                title: item.title,
                link: item.link,
                pubDate: item.pubDate || item.isoDate,
                content: fullContent,
                contentSnippet: item.contentSnippet,
                sourceId: source.id,
                sourceName: source.name,
                sourceType: source.type,
                isRepost,
                isReply
              };
            });
            allItems.push(...feedItems);
          }
        } catch (e) {
          console.error(`Failed to fetch ${source.name}:`, e);
        }
      }
      // Sort by date descending
      allItems.sort((a, b) => new Date(b.pubDate).getTime() - new Date(a.pubDate).getTime());
      setItems(allItems);
    } catch (error) {
      console.error("Error fetching feeds:", error);
    } finally {
      setLoading(false);
    }
  };

  const addSource = async () => {
    if (!newUrl) return;
    
    let type: 'rss' | 'x' = 'rss';
    let fetchUrl = newUrl;

    if (newUrl.includes('x.com') || newUrl.includes('twitter.com')) {
      type = 'x';
      const username = newUrl.split('/').pop()?.split('?')[0];
      if (username) {
        fetchUrl = `https://nitter.net/${username}/rss`;
      }
    }

    try {
      const res = await fetch(`/api/fetch-rss?url=${encodeURIComponent(fetchUrl)}`);
      if (res.ok) {
        const data = await res.json();
        const newSource: FeedSource = {
          id: Math.random().toString(36).substr(2, 9),
          name: type === 'x' ? `@${newUrl.split('/').pop()?.split('?')[0]}` : (data.title || 'New Source'),
          url: fetchUrl,
          type: type
        };
        setSources([...sources, newSource]);
        setNewUrl('');
        fetchFeeds();
      } else {
        const errorData = await res.json();
        alert(errorData.error || "Invalid feed URL");
      }
    } catch (error) {
      alert("Error adding source");
    }
  };

  const removeSource = (id: string) => {
    setSources(sources.filter(s => s.id !== id));
  };

  const handleSummarize = async (item: FeedItem) => {
    if (item.summary) return;
    
    setItems(prev => prev.map(i => i.id === item.id ? { ...i, isSummarizing: true } : i));
    if (selectedItem?.id === item.id) {
      setSelectedItem(prev => prev ? { ...prev, isSummarizing: true } : null);
    }

    const summary = await summarizeArticle(item.title, item.link);
    
    setItems(prev => prev.map(i => i.id === item.id ? { ...i, summary, isSummarizing: false } : i));
    if (selectedItem?.id === item.id) {
      setSelectedItem(prev => prev ? { ...prev, summary, isSummarizing: false } : null);
    }
  };

  const handleAutoSummarize = async (item: FeedItem) => {
    if (item.summary) return;
    
    setItems(prev => prev.map(i => i.id === item.id ? { ...i, isSummarizing: true } : i));
    if (selectedItem?.id === item.id) {
      setSelectedItem(prev => prev ? { ...prev, isSummarizing: true } : null);
    }

    const summary = await summarizeArticle(item.title, item.link);
    
    setItems(prev => prev.map(i => i.id === item.id ? { ...i, summary, isSummarizing: false } : i));
    if (selectedItem?.id === item.id) {
      setSelectedItem(prev => prev ? { ...prev, summary, isSummarizing: false } : null);
    }
  };

  const handleBatchSummarize = async () => {
    if (filteredItems.length === 0) return;
    setIsBatchSummarizing(true);
    setBatchSummary(null);
    
    try {
      // Group items by source
      const groups = filteredItems.reduce((acc, item) => {
        if (!acc[item.sourceName]) {
          acc[item.sourceName] = {
            type: item.sourceType,
            items: []
          };
        }
        acc[item.sourceName].items.push({
          title: item.title,
          content: item.content || item.contentSnippet || "",
          link: item.link
        });
        return acc;
      }, {} as Record<string, { type: 'rss' | 'news' | 'x', items: { title: string, content: string, link: string }[] }>);

      // Generate AI summary for each source individually
      const summaryPromises = Object.entries(groups).map(([name, group]) => 
        summarizeSource(name, group.type, group.items)
      );
      
      const summaries = await Promise.all(summaryPromises);
      
      // Combine them without using AI (simple concatenation)
      setBatchSummary(summaries.filter(s => s).join('\n\n---\n\n'));
    } catch (error) {
      console.error("Batch summarization error:", error);
      setBatchSummary("Failed to generate intelligence digest. Please try again.");
    } finally {
      setIsBatchSummarizing(false);
    }
  };

  const toggleSource = (id: string) => {
    setSelectedSourceIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const filteredItems = items.filter(item => {
    const isFromSelectedSource = selectedSourceIds.has(item.sourceId);
    const itemDate = new Date(item.pubDate).toISOString().split('T')[0];
    const isInRange = itemDate >= dateRange.start && itemDate <= dateRange.end;
    return isFromSelectedSource && isInRange;
  });

  const handleSelectItem = (item: FeedItem) => {
    setSelectedItem(item);
    // Automatically trigger summarization for RSS/News sources if not already present
    if (item.sourceType !== 'x' && !item.summary && !item.isSummarizing) {
      handleAutoSummarize(item);
    }
  };

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="border-b border-[var(--color-line)] p-6 flex justify-between items-center">
        <div>
          <h1 className="text-4xl font-serif italic tracking-tight">FeedDigest</h1>
          <p className="col-header mt-1">AI-Powered Intelligence Layer</p>
        </div>
        <div className="flex gap-4">
          <button 
            onClick={fetchFeeds}
            disabled={loading}
            className="p-2 hover:bg-[var(--color-ink)] hover:text-[var(--color-bg)] transition-colors rounded-full"
          >
            <RefreshCw className={cn("w-5 h-5", loading && "animate-spin")} />
          </button>
        </div>
      </header>

      <main className="flex-1 grid grid-cols-1 md:grid-cols-[300px_1fr_400px] overflow-hidden">
        {/* Sidebar: Sources */}
        <aside className="border-r border-[var(--color-line)] flex flex-col overflow-hidden">
          <div className="p-4 border-b border-[var(--color-line)]">
            <h2 className="col-header mb-4">Sources</h2>
            <div className="flex gap-2">
              <input 
                type="text" 
                placeholder="RSS URL"
                value={newUrl}
                onChange={(e) => setNewUrl(e.target.value)}
                className="flex-1 bg-transparent border border-[var(--color-line)] px-2 py-1 text-xs font-mono focus:outline-none"
              />
              <button onClick={addSource} className="p-1 border border-[var(--color-line)] hover:bg-[var(--color-ink)] hover:text-[var(--color-bg)]">
                <Plus className="w-4 h-4" />
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {sources.map(source => (
              <div key={source.id} className="flex items-center gap-2 p-2 text-xs font-mono border border-transparent hover:border-[var(--color-line)] group">
                <input 
                  type="checkbox" 
                  checked={selectedSourceIds.has(source.id)}
                  onChange={() => toggleSource(source.id)}
                  className="accent-[var(--color-ink)]"
                />
                <div className="flex items-center gap-2 truncate flex-1 cursor-pointer" onClick={() => toggleSource(source.id)}>
                  {source.type === 'x' && <Twitter className="w-3 h-3 text-sky-500" />}
                  <span className="truncate">{source.name}</span>
                </div>
                <button onClick={(e) => { e.stopPropagation(); removeSource(source.id); }} className="opacity-0 group-hover:opacity-100 p-1 hover:text-red-500">
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
            ))}
          </div>
        </aside>

        {/* Feed List & Range Summary */}
        <section className="flex flex-col overflow-hidden bg-white/30">
          <div className="p-6 border-b border-[var(--color-line)] space-y-4">
            <div className="flex flex-wrap items-end gap-4">
              <div className="space-y-1">
                <label className="col-header block">Start Date</label>
                <input 
                  type="date" 
                  value={dateRange.start}
                  onChange={(e) => setDateRange(prev => ({ ...prev, start: e.target.value }))}
                  className="bg-transparent border border-[var(--color-line)] px-2 py-1 text-xs font-mono focus:outline-none"
                />
              </div>
              <div className="space-y-1">
                <label className="col-header block">End Date</label>
                <input 
                  type="date" 
                  value={dateRange.end}
                  onChange={(e) => setDateRange(prev => ({ ...prev, end: e.target.value }))}
                  className="bg-transparent border border-[var(--color-line)] px-2 py-1 text-xs font-mono focus:outline-none"
                />
              </div>
              <button 
                onClick={handleBatchSummarize}
                disabled={isBatchSummarizing || filteredItems.length === 0}
                className="flex items-center gap-2 px-4 py-1.5 border border-[var(--color-line)] hover:bg-[var(--color-ink)] hover:text-[var(--color-bg)] transition-all disabled:opacity-30"
              >
                <Sparkles className={cn("w-4 h-4", isBatchSummarizing && "animate-pulse")} />
                <span className="text-[10px] font-mono uppercase tracking-widest">
                  {isBatchSummarizing ? "Synthesizing Digest..." : "Generate AI Digest"}
                </span>
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto">
            {loading && (
              <div className="p-12 text-center opacity-50 animate-pulse">
                <RefreshCw className="w-8 h-8 mx-auto mb-2 animate-spin" />
                <p className="font-mono text-xs uppercase">Synchronizing Feeds...</p>
              </div>
            )}

            {!loading && batchSummary && (
              <div className="p-8 border-b border-[var(--color-line)] bg-[var(--color-ink)] text-[var(--color-bg)]">
                <div className="flex items-center gap-2 mb-6 text-[10px] font-mono uppercase opacity-50">
                  <Sparkles className="w-3 h-3" />
                  <span>Intelligence Digest ({filteredItems.length} sources)</span>
                </div>
                <div className="markdown-body prose-invert text-sm max-w-none">
                  <Markdown>{batchSummary}</Markdown>
                </div>
              </div>
            )}

            {!loading && (
              <div className="grid grid-cols-[100px_1fr_80px] p-4 border-b border-[var(--color-line)] bg-[var(--color-bg)]/50">
                <span className="col-header">Source</span>
                <span className="col-header">Headline</span>
                <span className="col-header text-right">Date</span>
              </div>
            )}
            
            {!loading && filteredItems.map(item => (
              <div 
                key={item.id} 
                onClick={() => handleSelectItem(item)}
                className={cn(
                  "data-row grid grid-cols-[100px_1fr_80px] p-4 items-center",
                  selectedItem?.id === item.id && "bg-[var(--color-ink)] text-[var(--color-bg)]"
                )}
              >
                <span className="text-[10px] font-mono uppercase truncate pr-2">{item.sourceName}</span>
                <div className="flex flex-col truncate pr-4">
                  <span className="text-sm font-medium tracking-tight truncate">{item.title}</span>
                  {item.isRepost && (
                    <span className="text-[9px] font-mono uppercase opacity-40 flex items-center gap-1">
                      <RefreshCw className="w-2 h-2" /> Repost
                    </span>
                  )}
                  {item.isReply && (
                    <span className="text-[9px] font-mono uppercase opacity-40 flex items-center gap-1">
                      <ChevronRight className="w-2 h-2" /> Reply
                    </span>
                  )}
                </div>
                <span className="data-value text-[10px] text-right">
                  {new Date(item.pubDate).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                </span>
              </div>
            ))}
            
            {filteredItems.length === 0 && !loading && (
              <div className="p-24 text-center opacity-20">
                <Newspaper className="w-12 h-12 mx-auto mb-4" />
                <p className="font-serif italic text-lg">
                  {items.length === 0 ? "No articles found. Add sources or refresh." : "No articles match your filters."}
                </p>
                {items.length > 0 && (
                  <p className="text-xs font-mono mt-2 uppercase">
                    Total articles in cache: {items.length}
                  </p>
                )}
              </div>
            )}
          </div>
        </section>

        {/* Article Detail / Summary */}
        <aside className="border-l border-[var(--color-line)] flex flex-col overflow-hidden bg-white/50 backdrop-blur-sm">
          <AnimatePresence mode="wait">
            {selectedItem ? (
              <motion.div 
                key={selectedItem.id}
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 20 }}
                className="flex-1 flex flex-col overflow-hidden"
              >
                <div className="p-6 border-b border-[var(--color-line)]">
                  <div className="flex justify-between items-start mb-4">
                    <span className="col-header">{selectedItem.sourceName}</span>
                    <a href={selectedItem.link} target="_blank" rel="noopener noreferrer" className="p-1 hover:bg-[var(--color-ink)] hover:text-[var(--color-bg)] rounded">
                      <ExternalLink className="w-4 h-4" />
                    </a>
                  </div>
                  <h2 className="text-2xl font-serif italic leading-tight mb-4">{selectedItem.title}</h2>
                  
                  {!selectedItem.summary && (
                    <div className="grid grid-cols-1 gap-2">
                      <button 
                        onClick={() => handleSummarize(selectedItem)}
                        disabled={selectedItem.isSummarizing}
                        className="flex items-center justify-center gap-2 py-3 border border-[var(--color-line)] hover:bg-[var(--color-ink)] hover:text-[var(--color-bg)] transition-all group"
                      >
                        <Sparkles className={cn("w-4 h-4", selectedItem.isSummarizing && "animate-pulse")} />
                        <span className="text-[10px] font-mono uppercase tracking-widest">
                          {selectedItem.isSummarizing ? "Synthesizing..." : "AI Summary"}
                        </span>
                      </button>
                    </div>
                  )}
                </div>

                <div className="flex-1 overflow-y-auto p-6">
                  {selectedItem.isSummarizing ? (
                    <div className="p-12 text-center opacity-50 animate-pulse">
                      <RefreshCw className="w-8 h-8 mx-auto mb-2 animate-spin" />
                      <p className="font-mono text-[10px] uppercase">Synthesizing AI Summary...</p>
                    </div>
                  ) : selectedItem.summary ? (
                    <div className="markdown-body text-sm">
                      <div className="flex items-center gap-2 mb-6 text-[10px] font-mono uppercase opacity-50">
                        <Sparkles className="w-3 h-3" />
                        <span>AI Summary</span>
                      </div>
                      <Markdown>{selectedItem.summary}</Markdown>
                    </div>
                  ) : (
                    <div className="text-sm leading-relaxed">
                      <div 
                        className="prose prose-sm max-w-none opacity-80"
                        dangerouslySetInnerHTML={{ 
                          __html: selectedItem.content || selectedItem.contentSnippet || "No content available." 
                        }} 
                      />
                      {!selectedItem.summary && !selectedItem.isSummarizing && (
                        <p className="mt-4 text-[10px] font-mono uppercase opacity-30 italic">
                          Click 'AI Summary' for more content.
                        </p>
                      )}
                    </div>
                  )}
                </div>
              </motion.div>
            ) : (
              <div className="flex-1 flex items-center justify-center p-12 text-center opacity-20">
                <div>
                  <ChevronRight className="w-8 h-8 mx-auto mb-2" />
                  <p className="col-header">Select an article to begin</p>
                </div>
              </div>
            )}
          </AnimatePresence>
        </aside>
      </main>
    </div>
  );
}
