import express from "express";
import { createServer as createViteServer } from "vite";
import path from "path";
import Parser from "rss-parser";
import axios from "axios";
import { JSDOM } from "jsdom";
import { Readability } from "@mozilla/readability";
import puppeteer from "puppeteer-extra";
import StealthPlugin from "puppeteer-extra-plugin-stealth";

puppeteer.use(StealthPlugin());

async function startServer() {
  const app = express();
  const PORT = 3000;
  const parser = new Parser();

  app.use(express.json());

  // API route to fetch and extract main article content using Puppeteer (Browser-based)
  app.get("/api/fetch-content", async (req, res) => {
    const { url } = req.query;
    if (!url || typeof url !== "string") {
      return res.status(400).json({ error: "URL is required" });
    }

    let browser;
    try {
      console.log(`Launching browser to fetch: ${url}`);
      browser = await puppeteer.launch({
        headless: true,
        args: ["--no-sandbox", "--disable-setuid-sandbox"]
      });
      
      const page = await browser.newPage();
      
      // Set a realistic user agent
      await page.setUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36");
      
      // Navigate to the URL
      await page.goto(url, { waitUntil: "networkidle2", timeout: 30000 });
      
      // Get the page content
      const html = await page.content();
      
      const dom = new JSDOM(html, { url });
      const reader = new Readability(dom.window.document);
      const article = reader.parse();

      if (article) {
        res.json({
          title: article.title,
          content: article.textContent,
          excerpt: article.excerpt,
          byline: article.byline,
          siteName: article.siteName
        });
      } else {
        res.status(404).json({ error: "Could not extract main content from this page." });
      }
    } catch (error) {
      console.error("Error fetching content with Puppeteer:", error);
      const message = error instanceof Error ? error.message : "Unknown error";
      res.status(500).json({ error: `Failed to fetch content via browser: ${message}` });
    } finally {
      if (browser) {
        await browser.close();
      }
    }
  });

  // API route to fetch and parse RSS feed
  app.get("/api/fetch-rss", async (req, res) => {
    let { url } = req.query;
    if (!url || typeof url !== "string") {
      return res.status(400).json({ error: "URL is required" });
    }

    // Handle X.com / Twitter.com URLs by converting to Nitter RSS
    // We use a list of instances in case one is blocked or down
    const nitterInstances = [
      "nitter.net",
    ];

    if (url.includes("x.com") || url.includes("twitter.com")) {
      const username = url.split("/").pop()?.split("?")[0];
      if (username) {
        // Try instances until one works
        for (const instance of nitterInstances) {
          const proxyUrl = `https://${instance}/${username}/rss`;
          try {
            console.log(`Attempting to fetch from: ${proxyUrl}`);
            const feed = await parser.parseURL(proxyUrl);
            
            // Incorporate all posts including retweets and replies
            // We keep them all as requested by the user
            return res.json(feed);
          } catch (err) {
            console.warn(`Failed to fetch from ${instance}:`, err instanceof Error ? err.message : err);
            // Continue to next instance
          }
        }
        return res.status(500).json({ error: "All X.com proxies are currently unavailable. Please try again later or use a direct RSS feed." });
      }
    }

    try {
      console.log(`Fetching RSS from: ${url}`);
      const feed = await parser.parseURL(url);
      res.json(feed);
    } catch (error) {
      console.error("Error fetching RSS:", error);
      const message = error instanceof Error ? error.message : "Unknown error";
      res.status(500).json({ error: `Failed to fetch feed: ${message}` });
    }
  });

  // Vite middleware for development
  if (process.env.NODE_ENV !== "production") {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: "spa",
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), "dist");
    app.use(express.static(distPath));
    app.get("*", (req, res) => {
      res.sendFile(path.join(distPath, "index.html"));
    });
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`Server running on http://localhost:${PORT}`);
  });
}

startServer();
