// The in-browser contrast audit, as one payload.
//
// This exists as a committed file rather than as something retyped into a browser console
// per session, which is what happened during Brújula's first sweep and is recorded in
// docs/QA-BRUJULA.md as a thing not to repeat: a throwaway script cannot be re-run against
// the next state, so "we audited that" decays into "somebody audited something".
//
// **The WCAG maths here is deliberately not imported from anywhere.** Both theme suites
// recompute their declared ratios with the SAME helper the theme file uses, so a bug in that
// helper is invisible to them — it would move the measurement and the expectation together.
// That is not hypothetical here: `ratio()` in tests/test_huella_theme.py rounded to two
// decimals before comparing to a floor, which turned a real 2.9977 into exactly 3.0 and
// passed a failure. This implementation is written from the spec, in a different language,
// against what the browser actually painted rather than against what a token file declares.
// Agreement between the two is then evidence; agreement with itself would not have been.
//
// It also measures something the unit suites structurally cannot: `getComputedStyle` sees
// the cascade, inherited colour, and every rgba() layer composited in the real order. A
// token pair that is declared correctly and then rendered on the wrong ancestor is a bug
// only this can see.
//
// Usage: paste as the body of an evaluate_script / browser_evaluate call. Returns JSON.

() => {
  const AA_NORMAL = 4.5;
  const AA_LARGE = 3.0;

  const parse = (css) => {
    if (!css || css === "transparent") return null;
    const m = css.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(Number);
    if (p.length < 3 || p.slice(0, 3).some(Number.isNaN)) return null;
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };

  // Source-over compositing, straight alpha. Needed rather than "find the first opaque
  // ancestor" because this palette puts real semi-transparent tokens between the text and
  // its surface — GRID, LEVEL_BG's wells and the focus rings are all rgba() — and skipping
  // them measures a pair that is never on screen.
  const over = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  });

  // WCAG 2.x relative luminance, from the spec's own constants.
  const lum = (c) => {
    const lin = [c.r, c.g, c.b].map((v) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
  };

  const ratio = (a, b) => {
    const [la, lb] = [lum(a), lum(b)];
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  };

  // Walk to the root compositing every painted layer, so the answer is the colour actually
  // behind this text. Falls back to white only if the document itself declares nothing,
  // which is the browser's own default and would be a finding in its own right.
  const backdrop = (el) => {
    const stack = [];
    for (let n = el; n; n = n.parentElement) {
      const bg = parse(getComputedStyle(n).backgroundColor);
      if (bg && bg.a > 0) {
        stack.push(bg);
        if (bg.a === 1) break;
      }
    }
    let base = stack.length && stack[stack.length - 1].a === 1
      ? stack.pop()
      : { r: 255, g: 255, b: 255, a: 1 };
    for (let i = stack.length - 1; i >= 0; i--) base = over(stack[i], base);
    return base;
  };

  // A large-text threshold is a real WCAG allowance, not a loophole, but it only applies to
  // what the browser actually rendered — hence computed px and computed weight.
  const isLarge = (cs) => {
    const px = parseFloat(cs.fontSize);
    const w = parseInt(cs.fontWeight, 10) || 400;
    return px >= 24 || (px >= 18.66 && w >= 700);
  };

  const selector = (el) => {
    const bits = [];
    for (let n = el; n && n.nodeType === 1 && bits.length < 4; n = n.parentElement) {
      let s = n.tagName.toLowerCase();
      if (n.id) { bits.unshift(s + "#" + n.id); break; }
      const cls = (n.getAttribute("class") || "").trim().split(/\s+/).filter(Boolean);
      if (cls.length) s += "." + cls.slice(0, 2).join(".");
      bits.unshift(s);
    }
    return bits.join(" > ");
  };

  const results = [];
  const seen = new Set();

  for (const el of document.querySelectorAll("*")) {
    // Only elements that own rendered words. An element whose text lives in a child would
    // otherwise be measured once per ancestor, and the deepest one is the one that paints.
    const owns = Array.from(el.childNodes).some(
      (n) => n.nodeType === 3 && n.textContent.trim().length > 0,
    );
    if (!owns) continue;

    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none" || parseFloat(cs.opacity) === 0) continue;
    const box = el.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) continue;

    const fgRaw = parse(cs.color);
    if (!fgRaw) continue;
    const bg = backdrop(el);
    // Text can be translucent too, and then the pair on screen is the composite.
    const fg = fgRaw.a < 1 ? over(fgRaw, bg) : fgRaw;

    const r = ratio(fg, bg);
    const large = isLarge(cs);
    const floor = large ? AA_LARGE : AA_NORMAL;

    const hex = (c) =>
      "#" + [c.r, c.g, c.b].map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");

    const key = [hex(fg), hex(bg), Math.round(parseFloat(cs.fontSize)), cs.fontWeight].join("|");

    const row = {
      selector: selector(el),
      text: el.textContent.trim().slice(0, 60),
      color: hex(fg),
      background: hex(bg),
      ratio: Math.round(r * 10000) / 10000,
      fontPx: Math.round(parseFloat(cs.fontSize) * 100) / 100,
      weight: cs.fontWeight,
      large,
      floor,
      passes: r >= floor,
    };

    // Report every distinct PAIR once — the same token pair repeated down eighty rows is one
    // finding, and eighty copies of it is how a real second finding gets missed.
    if (!seen.has(key)) {
      seen.add(key);
      results.push(row);
    } else if (!row.passes) {
      const prior = results.find((x) => [x.color, x.background, Math.round(x.fontPx), x.weight].join("|") === key);
      if (prior) prior.occurrences = (prior.occurrences || 1) + 1;
    }
  }

  const failures = results.filter((r) => !r.passes).sort((a, b) => a.ratio - b.ratio);

  return {
    url: location.href,
    viewport: { w: innerWidth, h: innerHeight },
    distinctPairs: results.length,
    failures,
    // A page where nothing was measurable is not a page that passed. The sweep asserts on
    // this rather than on `failures.length === 0` alone.
    measured: results.length > 0,
    worst: results.length
      ? results.reduce((a, b) => (a.ratio < b.ratio ? a : b))
      : null,
  };
}
