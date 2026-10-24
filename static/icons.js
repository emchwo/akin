/* The 8 Akin type icons. One style for all: 64x64 viewBox, single-weight 1.25px strokes, round caps
   and joins, no fills, charcoal with one small accent detail. Elements are listed in drawing order,
   so AkinIcons.draw() can animate them line by line. */

(function () {
  const INK = "#2E2A25";
  const ACCENT = { slate: "#4F6D85", clay: "#B5543C", sage: "#6B7F5E" };

  // Each icon: list of [svg element markup, accent name or null, dashed?]
  const ICONS = {
    soulmate: {
      name: "The Soulmate",
      parts: [
        ['<circle cx="32" cy="32" r="17"/>', null],
        ['<path d="M32 15 C24 23 40 41 32 49"/>', "clay"],
      ],
    },
    stargazer: {
      name: "The Stargazer",
      parts: [
        ['<path d="M8 48 H56"/>', null],
        ['<path d="M17 47 Q22 34 35 26" stroke-dasharray="0.01 3.4" opacity="0.7"/>', null, true],
        ['<path d="M40 12 L41.5 15.9 L45.7 16.1 L42.5 18.8 L43.5 22.9 L40 20.6 L36.5 22.9 L37.5 18.8 L34.3 16.1 L38.5 15.9 Z"/>', "slate"],
      ],
    },
    anchor: {
      name: "The Anchor",
      parts: [
        ['<circle cx="32" cy="12" r="4"/>', "slate"],
        ['<path d="M32 16 V54"/>', null],
        ['<path d="M24 23 H40"/>', null],
        ['<path d="M16 38 Q18 52 32 54 Q46 52 48 38"/>', null],
        ['<path d="M12.5 41.5 L16 38 L19.5 41.5"/>', null],
        ['<path d="M44.5 41.5 L48 38 L51.5 41.5"/>', null],
      ],
    },
    ride_or_die: {
      name: "The Ride-or-Die",
      parts: [
        ['<path d="M6 24 H58"/>', null],
        ['<path d="M10 56 L31.1 26"/>', null],
        ['<path d="M54 56 L32.9 26"/>', null],
        ['<path d="M32 54 V31" stroke-dasharray="4.5 3.5"/>', "clay", true],
      ],
    },
    hearthseeker: {
      name: "The Hearthseeker",
      parts: [
        ['<path d="M18 44 V28 L32 16 L46 28 V44"/>', null],
        ['<path d="M15 44 H27 M37 44 H49"/>', null],
        ['<path d="M27 44 V33 H37 V44"/>', null],
        ['<path d="M32 42 C29.8 40.4 30.2 38.2 32 35.6 C33.8 38.2 34.2 40.4 32 42 Z"/>', "clay"],
        ['<path d="M29.5 46 C29 50.5 24 52.5 16 57 M34.5 46 C34.5 51 31 54 27 57"/>', null],
      ],
    },
    wayfarer: {
      name: "The Wayfarer",
      parts: [
        ['<path d="M8 55 C20 55 25 47 32 44 C39 41 45 35 41 29 C37 23 44 14 56 12"/>', null],
        ['<path d="M18 38.5 V44.5 M15 41.5 H21 M49 41 V47 M46 44 H52 M28 18 V24 M25 21 H31"/>', "slate"],
      ],
    },
    craftsman: {
      name: "The Craftsman",
      parts: [
        ['<path d="M18 46 V28 L32 16 L46 28"/>', null],
        ['<path d="M15 46 H49"/>', null],
        ['<path d="M24 32 H30 V38 H24 Z"/>', null],
        ['<path d="M46 29.5 V46" stroke-dasharray="2.4 3"/>', "clay", true],
      ],
    },
    co_founder: {
      name: "The Co-Founder",
      parts: [
        ['<path d="M12 56 L36 8"/>', null],
        ['<path d="M28 56 L52 8"/>', null],
        ['<path d="M17.5 45 H33.5 M22.5 35 H38.5 M27.5 25 H43.5"/>', null],
        ['<path d="M32.5 15 H48.5"/>', "sage"],
      ],
    },
  };

  function svgString(key, size) {
    const icon = ICONS[key];
    if (!icon) return "";
    const body = icon.parts
      .map(([markup, accent, dashed]) => {
        const color = accent ? ACCENT[accent] : INK;
        return markup.replace(/^<(\w+)/, `<$1 stroke="${color}"${dashed ? ' data-dashed="1"' : ""}`);
      })
      .join("");
    const dim = size ? ` width="${size}" height="${size}"` : "";
    return `<svg class="type-icon" viewBox="0 0 64 64"${dim} fill="none" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round" role="img" aria-label="${icon.name} icon" xmlns="http://www.w3.org/2000/svg">${body}</svg>`;
  }

  function element(key, size) {
    const tpl = document.createElement("template");
    tpl.innerHTML = svgString(key, size);
    return tpl.content.firstElementChild;
  }

  /* Draw an icon line by line (stroke animation). Dashed/dotted lines fade in at their turn, because
     their dash pattern can't also be used for the draw-on effect. Resolves when the drawing finishes. */
  function draw(svg, total = 1500) {
    const els = [...svg.querySelectorAll("path, circle, line, rect, polyline")];
    if (!els.length || !svg.animate) return Promise.resolve();
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) return Promise.resolve();
    const n = els.length;
    const dur = n === 1 ? total : total * 0.5;
    const gap = n === 1 ? 0 : (total - dur) / (n - 1);
    const anims = els.map((el, i) => {
      const opts = { duration: dur, delay: i * gap, easing: "cubic-bezier(.45,.05,.35,1)", fill: "both" };
      if (el.dataset.dashed || typeof el.getTotalLength !== "function") {
        return el.animate([{ opacity: 0 }, { opacity: el.getAttribute("opacity") || 1 }], opts);
      }
      const len = el.getTotalLength() + 1;
      el.style.strokeDasharray = `${len}`;
      return el.animate([{ strokeDashoffset: len }, { strokeDashoffset: 0 }], opts);
    });
    return Promise.all(anims.map((a) => a.finished)).then(() => {
      els.forEach((el) => { if (!el.dataset.dashed) el.style.strokeDasharray = ""; });
    });
  }

  window.AkinIcons = { ICONS, svgString, element, draw, keys: Object.keys(ICONS) };
})();
