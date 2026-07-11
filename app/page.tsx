import type { Metadata } from "next";
import { ProposalApp } from "./proposal-app";

export const metadata: Metadata = {
  title: "3GPP 提案洞察",
  description: "本地优先的 3GPP 提案下载、分析、问答与报告工具。",
};

export default function Home() {
  return <ProposalApp />;
}
