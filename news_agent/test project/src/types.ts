export interface FeedSource {
  id: string;
  url: string;
  name: string;
  type: 'rss' | 'news' | 'x';
}

export interface FeedItem {
  id: string;
  title: string;
  link: string;
  pubDate: string;
  content: string;
  contentSnippet?: string;
  sourceId: string;
  sourceName: string;
  sourceType: 'rss' | 'news' | 'x';
  summary?: string;
  isSummarizing?: boolean;
  isRepost?: boolean;
  isReply?: boolean;
}
