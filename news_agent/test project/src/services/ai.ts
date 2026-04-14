import { GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY || "" });

export async function summarizeArticle(title: string, url: string) {
  try {
    const response = await ai.models.generateContent({
      model: "gemini-3-flash-preview",
      contents: `Please provide a concise, professional summary of the following article in 200 words or less.
                 Title: ${title}
                 URL: ${url}`,
      config: {
        systemInstruction: "You are a senior intelligence analyst. Your goal is to provide a concise, professional summary of an article. Focus on the key takeaways and implications. Use the urlContext tool to fetch the full article content from the provided URL.",
        tools: [{ urlContext: {} }] as any
      }
    });

    return response.text || "Could not generate summary.";
  } catch (error) {
    console.error(`Summarization error for ${url}:`, error);
    return "Error generating summary.";
  }
}

export async function summarizeSource(sourceName: string, sourceType: 'rss' | 'news' | 'x', items: { title: string; content: string; link: string }[]) {
  try {
    if (items.length === 0) return "";

    // Limit to 20 items to stay within Gemini's urlContext tool limit
    const limitedItems = items.slice(0, 20);

    const formattedItems = limitedItems.map((item, i) => 
      `Item ${i + 1}:\nTitle: ${item.title}\nURL: ${item.link}\nContent: ${item.content.replace(/<[^>]*>?/gm, ' ').substring(0, 1000)}`
    ).join('\n\n');

    const prompt = sourceType === 'x' 
      ? `Summarize the following posts from ${sourceName} on X.com. 
         Identify the main topics discussed and the overall attitude/opinion/input of the account.
         Format: "[Account Name]: 
         [Discussion on Topic A] [account's opinion/attitude/input on Topic A]
         [Discussion on Topic B] [account's opinion/attitude/input on Topic B] ... "`
      : `Provide a brief summary/abstract for each of the following articles from ${sourceName}.
         Format: "[Source Name]: 
         [Article Title]: [Abstract < 200 words]"`;

    const response = await ai.models.generateContent({
      model: "gemini-3-flash-preview",
      contents: `${prompt}\n\nItems:\n${formattedItems}`,
      config: {
        systemInstruction: "You are a senior intelligence analyst. Your goal is to provide concise, professional summaries for intelligence reports. Use the urlContext tool to fetch full article content when needed.",
        tools: [{ urlContext: {} }] as any
      }
    });

    return response.text || `Could not generate summary for ${sourceName}.`;
  } catch (error) {
    console.error(`Summarization error for ${sourceName}:`, error);
    return `Error summarizing ${sourceName}.`;
  }
}
