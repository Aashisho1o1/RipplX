import { describe, expect, it } from "vitest";
import global from "./global.css?raw";
import tokens from "./tokens.css?raw";


function block(selector: string): string {
  const start = global.indexOf(selector);
  expect(start, `missing ${selector}`).toBeGreaterThanOrEqual(0);
  return global.slice(start, global.indexOf("}", start) + 1);
}

function token(name: string): string {
  const match = tokens.match(new RegExp(`${name}:\\s*([^;]+);`));
  expect(match, `missing ${name}`).not.toBeNull();
  const value = match?.[1];
  if (!value) throw new Error(`missing ${name}`);
  return value.trim();
}

type RGB = [number, number, number];
const hex = (value: string): RGB => [
  Number.parseInt(value.slice(1, 3), 16),
  Number.parseInt(value.slice(3, 5), 16),
  Number.parseInt(value.slice(5, 7), 16),
];
const luminance = (rgb: RGB) => {
  const values = rgb.map(value => {
    const channel = value / 255;
    return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  }) as RGB;
  return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2];
};
const contrast = (left: RGB, right: RGB) => {
  const leftLuminance = luminance(left);
  const rightLuminance = luminance(right);
  const light = Math.max(leftLuminance, rightLuminance);
  const dark = Math.min(leftLuminance, rightLuminance);
  return (light + 0.05) / (dark + 0.05);
};
const composite = (foreground: RGB, alpha: number, background: RGB): RGB => [
  Math.round(foreground[0] * alpha + background[0] * (1 - alpha)),
  Math.round(foreground[1] * alpha + background[1] * (1 - alpha)),
  Math.round(foreground[2] * alpha + background[2] * (1 - alpha)),
];

describe("accessible restrained design tokens", () => {
  const creamTokens = [
    "--color-bg", "--color-canvas", "--color-panel", "--color-panel-alt",
    "--color-panel-deep", "--color-accent-wash", "--color-warn-wash",
  ];

  it("gives focusable controls a 3:1 indicator on cream and the dark rail", () => {
    const focus = block("button:focus-visible");
    expect(focus).toContain("outline: 2px solid var(--color-accent-strong)");
    expect(focus).toContain("box-shadow: 0 0 0 2px var(--color-panel)");
    expect(block(".filing-option:focus-within")).toContain("outline: 2px solid");
    expect(global).not.toContain("rgba(22, 117, 95, 0.24)");
    for (const surface of creamTokens) {
      expect(contrast(hex(token("--color-accent-strong")), hex(token(surface)))).toBeGreaterThanOrEqual(3);
    }
    expect(contrast(hex(token("--color-panel")), hex(token("--color-sidebar")))).toBeGreaterThanOrEqual(3);
  });

  it("uses 3:1 control borders while decorative hairlines remain quiet", () => {
    for (const surface of creamTokens) {
      const background = hex(token(surface));
      expect(contrast(composite(hex("#14231e"), 0.52, background), background)).toBeGreaterThanOrEqual(3);
    }
    const panel = hex(token("--color-panel"));
    expect(contrast(composite(hex("#14231e"), 0.11, panel), panel)).toBeLessThan(2);
    expect(contrast(composite(hex("#14231e"), 0.19, panel), panel)).toBeLessThan(2);
    for (const selector of [".proof-toggle", "\n.input {", "\n.filing-option {"]) {
      expect(block(selector)).toContain("border: var(--border-control)");
    }
  });

  it("keeps the hidden filing radio focusable inside its selected row", () => {
    expect(block(".filing-option {")).toContain("position: relative");
    const input = block(".filing-option input");
    expect(input).toContain("opacity: 0");
    expect(input).not.toContain("display: none");
    expect(input).not.toContain("visibility: hidden");
    expect(block(".filing-option.selected")).toContain("border-color: var(--color-accent)");
  });

  it("references every declared token and keeps a five-step serif scale", () => {
    const declared = [...tokens.matchAll(/(--[\w-]+):/g)].flatMap(match => match[1] ? [match[1]] : []);
    for (const name of declared) expect(`${tokens}\n${global}`).toContain(`var(${name})`);
    expect(declared.filter(name => name.startsWith("--text-serif-"))).toHaveLength(5);
    expect(tokens).not.toContain("--color-faint:");
    expect(global).not.toContain("font-size: clamp(");
    expect(block(".answer-hero")).toContain("var(--text-serif-xl)");
    expect(block(".quote")).toContain("var(--text-serif-lg)");
  });
});
