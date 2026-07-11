import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "3GPP 提案洞察",
  description: "下载、分析并理解一整场 3GPP 会议的提案。",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
