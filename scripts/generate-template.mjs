import pptxgen from "pptxgenjs";
import fs from "node:fs";
import path from "node:path";

// ============================================================
// 3GPP 提案分析 PPT 模板生成器  (Light 版)
// ============================================================
// 第一页：各公司总结 (Key Messages)
// 后续每页：单个公司的提案分析详情
// ============================================================

// ---------- 配色方案 (浅色系) ----------
const C = {
  primary:     "1E40AF",   // 深邃蓝 — 标题、重点文字、侧边栏
  primaryMid:  "2563EB",   // 中蓝 — 装饰条、小标签
  primaryPale: "DBEAFE",   // 极淡蓝 — 卡片底色
  accent:      "0D9488",   // 青绿 — 圆形图标、强调分隔线
  accentPale:  "CCFBF1",   // 淡青 — 轻量背景区块
  bg:          "FFFFFF",   // 纯白 — 主背景
  bgWarm:      "F8FAFC",   // 微暖灰 — 详情页底色
  body:        "1E293B",   // 深灰 — 正文
  muted:       "94A3B8",   // 中灰 — 次要信息
  border:      "E2E8F0",   // 浅灰 — 分隔线
  white:       "FFFFFF",
  tagBg:       "1E40AF",   // 提案编号标签底色
  tagText:     "DBEAFE",   // 提案编号标签文字
};

const HEAD = "Aptos Display";
const BODY = "Aptos";

const makeShadow = () => ({ type: "outer", blur: 10, offset: 2, angle: 135, color: "000000", opacity: 0.06 });

// ---------- 工具 ----------
function addFooter(slide, pageNum, totalPages) {
  slide.addShape("rect", { x: 0, y: 7.22, w: 13.33, h: 0.28, fill: { color: C.border } });
  slide.addText(`${pageNum} / ${totalPages}`, {
    x: 12.1, y: 7.22, w: 1.0, h: 0.28,
    fontSize: 8, color: C.muted, align: "right", valign: "middle", margin: 0, fontFace: BODY,
  });
  slide.addText("3GPP Proposal Analyzer  ·  Confidential", {
    x: 0.5, y: 7.22, w: 5, h: 0.28,
    fontSize: 7, color: C.muted, align: "left", valign: "middle", margin: 0, fontFace: BODY,
  });
}

// ============================================================
// 示例数据
// ============================================================
const data = {
  title: "3GPP RAN1 #118-bis 提案分析报告",
  subtitle: "2025年1月  ·  Release 19 NR MIMO Evolution",
  companies: [
    {
      name: "华为 (Huawei)",
      logo: "H",
      keyMessage: "力推 CJT (Coherent Joint Transmission) 作为 Rel-19 核心特性，提供完整端到端方案及仿真结果",
      proposals: ["R1-2400xxx", "R1-2401xxx"],
      summary: {
        title: "华为提案主要内容总结",
        points: [
          "主张 CJT 作为 Rel-19 MIMO 演进的最高优先级特性，提交了 Phase 1 完整技术框架",
          "提出基于多 TRP 的相干联合传输方案，包含 CSI 反馈增强、DMRS 设计和 QCL 假设",
          "给出了系统级仿真结果：CJT 相比 NCJT 小区边缘吞吐量提升 35%，平均吞吐量提升 18%",
          "建议优先标准化 Type II 码本增强和 CJT 测量框架，并提供了详细的 WF (Work Plan) 时间表",
          "联合多家运营商（中国移动、德国电信）共同推动，形成强生态支持阵营",
        ],
      },
      highlight: "CJT 技术方案最完整，仿真数据充分，拥有运营商阵营支持",
    },
    {
      name: "爱立信 (Ericsson)",
      logo: "E",
      keyMessage: "支持多 TRP 增强但更侧重 NCJT 演进路径，对 CJT 持审慎态度",
      proposals: ["R1-2402xxx", "R1-2402yyy"],
      summary: {
        title: "爱立信提案主要内容总结",
        points: [
          "认可多 TRP 作为 Rel-19 重要方向，但主张 NCJT 优先于 CJT，认为 CJT 商用条件不成熟",
          "提出基于 S-DCI 的 NCJT 增强方案，重点优化回传时延鲁棒性和调度灵活性",
          "反对过早标准化 CJT Phase 2（多用户 CJT），建议先完成 Phase 1 的仿真验证后再决策",
          "强调与现有 NR 标准的后向兼容性，避免对已有终端造成影响",
          "与诺基亚、三星在多个议题上形成联合提案，形成技术路线竞争态势",
        ],
      },
      highlight: "NCJT vs CJT 路线之争：主张渐进式演进，对 CJT 标准化范围有分歧",
    },
    {
      name: "诺基亚 (Nokia)",
      logo: "N",
      keyMessage: "聚焦 SRS 增强和天线端口扩展，支持多 TRP 但不站队 CJT/NCJT",
      proposals: ["R1-2403xxx"],
      summary: {
        title: "诺基亚提案主要内容总结",
        points: [
          "核心贡献集中在 SRS (Sounding Reference Signal) 增强，支持更多天线端口 (最大 8T8R 提升至 16T16R)",
          "提出 SRS 时域捆绑和跳频增强方案，提升信道估计精度和覆盖范围",
          "对 CJT vs NCJT 保持中立，主张两者并行推进，不排斥任何技术方案",
          "联合爱立信推动 M-TRP 的 QCL/TCI 框架统一，减少标准化碎片化",
          "提出 AI/ML for NR Air Interface 的研究方向，聚焦信道预测和波束管理",
        ],
      },
      highlight: "在核心争论中保持中立，专注 SRS 和 AI/ML 两个细分技术点",
    },
    {
      name: "高通 (Qualcomm)",
      logo: "Q",
      keyMessage: "强推 AI/ML 空口优化，在波束管理和 CSI 压缩领域提交多个关键方案",
      proposals: ["R1-2404xxx", "R1-2404yyy", "R1-2404zzz"],
      summary: {
        title: "高通提案主要内容总结",
        points: [
          "重点推动 AI/ML for NR Air Interface，提交了两个用例：CSI 压缩和波束预测",
          "CSI 压缩方案：基于自编码器 (Autoencoder) 将 CSI 反馈开销降低 40%，同时保持 95% 精度",
          "波束管理增强：提出基于 AI 的波束预测方案，减少 50% 波束扫描开销",
          "提供完整的 AI/ML 生命周期管理框架（训练、推理、更新），并给出了标准化影响分析",
          "与多家芯片厂商（联发科、三星）形成技术协作，推动 AI/ML 成为 Rel-19 Normative 特性",
        ],
      },
      highlight: "AI/ML 空口优化领跑者，有完整生命周期管理方案和跨厂商生态",
    },
  ],
};

// ============================================================
// 生成
// ============================================================
const totalSlides = data.companies.length + 1;
const deck = new pptxgen();
deck.layout = "LAYOUT_WIDE";
deck.author = "3GPP Proposal Analyzer";
deck.title = data.title;
deck.subject = "3GPP Proposal Analysis";
deck.company = "Proposal Insight";
deck.theme = { headFontFace: HEAD, bodyFontFace: BODY };

// ============================
// Slide 1 — 总结页
// ============================
{
  const slide = deck.addSlide();
  slide.background = { color: C.bg };

  // top accent bar
  slide.addShape("rect", { x: 0, y: 0, w: 13.33, h: 0.07, fill: { color: C.accent } });

  // title
  slide.addText(data.title, {
    x: 0.9, y: 0.75, w: 11.5, h: 0.7,
    fontFace: HEAD, fontSize: 30, bold: true, color: C.primary, margin: 0,
  });

  // subtitle
  slide.addText(data.subtitle, {
    x: 0.9, y: 1.45, w: 11.5, h: 0.4,
    fontFace: BODY, fontSize: 14, color: C.muted, margin: 0,
  });

  // separator
  slide.addShape("rect", { x: 0.9, y: 2.05, w: 2.2, h: 0.025, fill: { color: C.accent } });

  // section label
  slide.addText("KEY MESSAGES  ·  各公司核心立场", {
    x: 0.9, y: 2.35, w: 8, h: 0.4,
    fontFace: BODY, fontSize: 10, color: C.muted, charSpacing: 3, margin: 0,
  });

  // cards grid
  const cardW = 5.5;
  const cardH = 2.15;
  const startX = 0.9;
  const startY = 2.95;
  const gapX = 0.5;
  const gapY = 0.35;

  data.companies.forEach((comp, i) => {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const cx = startX + col * (cardW + gapX);
    const cy = startY + row * (cardH + gapY);

    // card bg
    slide.addShape("rect", {
      x: cx, y: cy, w: cardW, h: cardH,
      fill: { color: C.primaryPale },
      shadow: makeShadow(),
    });

    // left accent bar
    slide.addShape("rect", {
      x: cx, y: cy, w: 0.065, h: cardH,
      fill: { color: C.accent },
    });

    // circle + initial
    slide.addShape("oval", {
      x: cx + 0.35, y: cy + 0.3, w: 0.5, h: 0.5,
      fill: { color: C.accent },
    });
    slide.addText(comp.logo, {
      x: cx + 0.35, y: cy + 0.3, w: 0.5, h: 0.5,
      fontSize: 17, color: C.white, fontFace: HEAD, bold: true,
      align: "center", valign: "middle", margin: 0,
    });

    // company name
    slide.addText(comp.name, {
      x: cx + 1.05, y: cy + 0.3, w: cardW - 1.5, h: 0.4,
      fontSize: 15, color: C.primary, fontFace: HEAD, bold: true, margin: 0,
    });

    // key message
    slide.addText(comp.keyMessage, {
      x: cx + 0.35, y: cy + 0.95, w: cardW - 0.7, h: 1.05,
      fontSize: 11.5, color: C.body, fontFace: BODY, margin: 0,
      valign: "top", lineSpacingMultiple: 1.3,
    });
  });

  // footer
  slide.addShape("rect", { x: 0, y: 7.22, w: 13.33, h: 0.28, fill: { color: C.border } });
  slide.addText("3GPP Proposal Analyzer  ·  Confidential", {
    x: 0.5, y: 7.22, w: 5, h: 0.28,
    fontSize: 7, color: C.muted, align: "left", valign: "middle", margin: 0, fontFace: BODY,
  });
}

// ============================
// Slides 2–N — 公司详情页
// ============================
data.companies.forEach((comp, i) => {
  const slide = deck.addSlide();
  const pageNum = i + 2;
  slide.background = { color: C.bgWarm };

  // ---- left sidebar (light) ----
  const sidebarW = 3.2;

  slide.addShape("rect", {
    x: 0, y: 0, w: sidebarW, h: 7.5,
    fill: { color: C.primary },
  });

  // subtle sidebar texture line
  slide.addShape("rect", {
    x: sidebarW - 0.06, y: 0, w: 0.06, h: 7.5,
    fill: { color: C.accent },
  });

  // circle
  slide.addShape("oval", {
    x: 0.8, y: 1.0, w: 1.1, h: 1.1,
    fill: { color: C.accent },
  });
  slide.addText(comp.logo, {
    x: 0.8, y: 1.0, w: 1.1, h: 1.1,
    fontSize: 36, color: C.white, fontFace: HEAD, bold: true,
    align: "center", valign: "middle", margin: 0,
  });

  // name
  slide.addText(comp.name, {
    x: 0.55, y: 2.35, w: sidebarW - 1.1, h: 0.5,
    fontSize: 20, color: C.white, fontFace: HEAD, bold: true, margin: 0, align: "center",
  });

  // divider
  slide.addShape("rect", { x: 1.1, y: 3.0, w: 1.0, h: 0.025, fill: { color: C.accentPale } });

  // count
  slide.addText(`提案 ${comp.proposals.length} 篇`, {
    x: 0.55, y: 3.25, w: sidebarW - 1.1, h: 0.4,
    fontSize: 11, color: C.accentPale, fontFace: BODY, margin: 0, align: "center",
  });

  // highlight label
  slide.addText("核心立场", {
    x: 0.55, y: 3.9, w: sidebarW - 1.1, h: 0.35,
    fontSize: 9, color: C.accentPale, fontFace: BODY, charSpacing: 3, margin: 0, align: "center",
  });
  slide.addText(comp.highlight, {
    x: 0.4, y: 4.25, w: sidebarW - 0.8, h: 2.0,
    fontSize: 10, color: "DBEAFE", fontFace: BODY, margin: 0, align: "center",
    valign: "top", lineSpacingMultiple: 1.3,
  });

  // ---- main content ----
  const contentX = sidebarW + 0.7;
  const contentW = 13.33 - contentX - 0.5;

  // page label
  slide.addText(`${String(pageNum).padStart(2, "0")}  ·  公司提案分析`, {
    x: contentX, y: 0.4, w: contentW, h: 0.35,
    fontSize: 8, color: C.muted, fontFace: BODY, charSpacing: 2, margin: 0,
  });

  // section title
  slide.addText(comp.summary.title, {
    x: contentX, y: 0.85, w: contentW, h: 0.6,
    fontSize: 24, color: C.primary, fontFace: HEAD, bold: true, margin: 0,
  });

  // separator
  slide.addShape("rect", { x: contentX, y: 1.5, w: 1.8, h: 0.03, fill: { color: C.accent } });

  // proposal tags
  const tagY = 1.8;
  comp.proposals.forEach((pid, pi) => {
    const tagW = pid.length * 0.09 + 0.3;
    const tagX = contentX + pi * 1.65;
    slide.addShape("rect", {
      x: tagX, y: tagY, w: tagW, h: 0.28,
      fill: { color: C.primaryMid },
    });
    slide.addText(pid, {
      x: tagX, y: tagY, w: tagW, h: 0.28,
      fontSize: 8, color: C.white, fontFace: BODY,
      align: "center", valign: "middle", margin: 0,
    });
  });

  // bullet points
  const bulletTexts = comp.summary.points.map((pt, pi) => ({
    text: pt,
    options: { bullet: { code: "25CF" }, breakLine: pi < comp.summary.points.length - 1, fontSize: 13 },
  }));

  slide.addText(bulletTexts, {
    x: contentX, y: 2.35, w: contentW - 0.1, h: 4.4,
    fontFace: BODY, fontSize: 13, color: C.body, margin: [0, 0, 0, 10],
    valign: "top", paraSpaceAfterPt: 10, lineSpacingMultiple: 1.25,
  });

  addFooter(slide, pageNum, totalSlides);
});

// ============================================================
// 输出
// ============================================================
const outputPath = "/Users/shrice/Documents/提案分析工具/output/3GPP提案分析模板.pptx";
fs.mkdirSync(path.dirname(outputPath), { recursive: true });
await deck.writeFile({ fileName: outputPath });
console.log(`✅ 模板已生成: ${outputPath}`);
console.log(`   共 ${totalSlides} 页 (1 页总结 + ${data.companies.length} 页公司分析)`);
