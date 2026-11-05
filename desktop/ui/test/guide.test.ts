import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { test } from "node:test";
import { type Block, imageUrl, parseGuide, parseInline } from "../src/guide.ts";

const GUIDES = new URL("../../../docs/guides/", import.meta.url);

test("inline text keeps its bold, italics, code and web links", () => {
  assert.deepEqual(parseInline("先 **确认** 再 *开始*，填 `Story_3`，见 [攻略](https://example.com/a)。"), [
    { kind: "text", text: "先 " },
    { kind: "strong", text: "确认" },
    { kind: "text", text: " 再 " },
    { kind: "em", text: "开始" },
    { kind: "text", text: "，填 " },
    { kind: "code", text: "Story_3" },
    { kind: "text", text: "，见 " },
    { kind: "link", text: "攻略", href: "https://example.com/a" },
    { kind: "text", text: "。" },
  ]);
});

test("a link that is not a web address stays as text", () => {
  for (const source of ["[点我](javascript:alert(1))", "[文件](../secret.txt)"]) {
    assert.ok(parseInline(source).every((part) => part.kind === "text"), source);
  }
});

test("a guide comes apart into sections, pictures and numbered steps that keep their numbers", () => {
  const blocks = parseGuide([
    "## 老手须知",
    "",
    "1. **固定位置**：见图一。",
    "",
    "[![图一：仓库](../images/usage/trade-warehouse.png)](../images/usage/trade-warehouse.png)",
    "",
    "2. 设置技能。",
    "- 砍价",
    "- 料理",
  ].join("\r\n"));
  assert.deepEqual(blocks.map((block) => block.kind), ["heading", "list", "image", "list", "list"]);
  assert.deepEqual(blocks[0], { kind: "heading", text: "老手须知", id: "guide-section-1" });
  assert.deepEqual(blocks[2], { kind: "image", alt: "图一：仓库", src: "/guide-images/trade-warehouse.png" });
  assert.equal((blocks[3] as Extract<Block, { kind: "list" }>).start, 2);
  assert.equal((blocks[4] as Extract<Block, { kind: "list" }>).ordered, false);
});

test("a picture's address names only the file", () => {
  assert.equal(imageUrl("../../../secret/a b.png"), "/guide-images/a%20b.png");
});

test("every shipped guide parses with nothing left as raw Markdown", () => {
  const files = readdirSync(GUIDES).filter((name) => name.endsWith(".md"));
  assert.equal(files.length, 3);
  for (const name of files) {
    const blocks = parseGuide(readFileSync(new URL(name, GUIDES), "utf-8"));
    assert.ok(blocks.some((block) => block.kind === "heading"), name);
    assert.ok(blocks.some((block) => block.kind === "image"), name);
    for (const block of blocks) {
      const parts = block.kind === "paragraph" ? block.content : block.kind === "list" ? block.items.flat() : [];
      for (const part of parts) {
        if (part.kind === "text") {
          assert.doesNotMatch(part.text, /\*\*|!\[|\]\(/, `${name}: ${part.text}`);
        }
      }
    }
  }
});
