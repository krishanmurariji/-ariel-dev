// Horilla AI Recruitment: scroll-driven 3D scene (Three.js, self-hosted).
// Four robot characters, one per module, a camera that glides down the page, and mouse parallax.
// If WebGL is missing or the user prefers reduced motion, the page falls back to plain content.
import * as THREE from './three.module.min.js';

const root = document.documentElement;
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

function webglOk() {
  try {
    const c = document.createElement('canvas');
    return !!(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
  } catch (e) { return false; }
}

// ---- reveal-on-scroll for text panels (works with or without 3D) -----------------------------
const stages = [...document.querySelectorAll('[data-stage]')];
const io = new IntersectionObserver((entries) => {
  for (const e of entries) if (e.isIntersecting) e.target.classList.add('in');
}, { threshold: 0.25 });
document.querySelectorAll('.panel, .hero .copy').forEach((el) => io.observe(el));

// ---- side dots --------------------------------------------------------------------------------
const dotsEl = document.getElementById('dots');
const dotBtns = stages.map((s, i) => {
  const b = document.createElement('button');
  b.setAttribute('aria-label', s.dataset.label || ('Section ' + (i + 1)));
  b.title = s.dataset.label || '';
  b.addEventListener('click', () => s.scrollIntoView({ behavior: 'smooth', block: 'center' }));
  dotsEl.appendChild(b);
  return b;
});

let activeIdx = -1;
function setActive(i) {
  if (i === activeIdx) return;
  activeIdx = i;
  dotBtns.forEach((b, k) => b.classList.toggle('on', k === i));
  const rgb = stages[i] && stages[i].dataset.accent;
  if (rgb) { root.style.setProperty('--accent-rgb', rgb); root.style.setProperty('--accent', 'rgb(' + rgb + ')'); }
}
setActive(0);

if (reduce || !webglOk()) {
  root.classList.add('no-3d');
} else {
  try { init(); } catch (err) { console.error('3D scene failed, using plain page', err); root.classList.add('no-3d'); }
}

// =================================================================================================
function init() {
  const canvas = document.getElementById('scene');
  const isCoarse = matchMedia('(pointer: coarse)').matches;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, isCoarse ? 1.5 : 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x090f2b, 0.016);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 220);

  // lights
  scene.add(new THREE.HemisphereLight(0xbfd4ff, 0x1a1f4a, 1.15));
  const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(4, 7, 9); scene.add(key);
  const rimA = new THREE.PointLight(0x38bdf8, 40, 30); rimA.position.set(-7, 3, -2); scene.add(rimA);
  const rimB = new THREE.PointLight(0xf472b6, 34, 30); rimB.position.set(7, -2, -3); scene.add(rimB);
  const rig = new THREE.Group(); scene.add(rig); // lights follow the camera vertically
  rig.add(key, rimA, rimB);

  const SPACING = 10;

  // ---- helpers --------------------------------------------------------------------------------
  const smooth = (x) => { x = Math.min(1, Math.max(0, x)); return x * x * (3 - 2 * x); };
  const lerp = (a, b, t) => a + (b - a) * t;

  function canvasTex(w, h, draw) {
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    const ctx = c.getContext('2d'); draw(ctx, w, h);
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 4;
    return { tex, ctx, canvas: c };
  }
  function rr(x, px, py, w, h, r) {
    x.beginPath(); x.moveTo(px + r, py);
    x.arcTo(px + w, py, px + w, py + h, r); x.arcTo(px + w, py + h, px, py + h, r);
    x.arcTo(px, py + h, px, py, r); x.arcTo(px, py, px + w, py, r); x.closePath();
  }
  const planeMat = (tex, extra = {}) => new THREE.MeshBasicMaterial({ map: tex, transparent: true, side: THREE.DoubleSide, toneMapped: false, ...extra });

  // ---- the robot character --------------------------------------------------------------------
  function makeBot(accent, opts = {}) {
    const g = new THREE.Group();
    const white = new THREE.MeshStandardMaterial({ color: 0xf1f5ff, roughness: 0.32, metalness: 0.08 });
    const tint = new THREE.MeshStandardMaterial({ color: accent, roughness: 0.38, metalness: 0.2, emissive: accent, emissiveIntensity: 0.18 });
    const dark = new THREE.MeshStandardMaterial({ color: 0x0a1030, roughness: 0.12, metalness: 0.7 });
    const glow = new THREE.MeshBasicMaterial({ color: new THREE.Color(accent).lerp(new THREE.Color(0xffffff), 0.35), toneMapped: false });

    const body = new THREE.Group(); g.add(body);
    const torso = new THREE.Mesh(new THREE.CapsuleGeometry(0.62, 0.7, 8, 28), white); torso.position.y = -0.1; body.add(torso);
    const belt = new THREE.Mesh(new THREE.TorusGeometry(0.63, 0.06, 12, 40), tint); belt.rotation.x = Math.PI / 2; belt.position.y = -0.55; body.add(belt);
    const core = new THREE.Mesh(new THREE.SphereGeometry(0.2, 20, 16), glow); core.scale.set(1, 1, 0.35); core.position.set(0, 0.0, 0.6); body.add(core);

    // thruster + hover ring
    const thr = new THREE.Mesh(new THREE.ConeGeometry(0.36, 0.5, 24), tint); thr.rotation.x = Math.PI; thr.position.y = -1.3; body.add(thr);
    const flame = new THREE.Mesh(new THREE.ConeGeometry(0.2, 0.7, 20), glow); flame.rotation.x = Math.PI; flame.position.y = -1.85; body.add(flame);
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.9, 0.035, 10, 60), glow); ring.rotation.x = Math.PI / 2; ring.position.y = -2.15; body.add(ring);
    const ring2 = ring.clone(); ring2.scale.set(1.35, 1.35, 1.35); ring2.material = glow.clone(); ring2.material.transparent = true; ring2.material.opacity = 0.35; body.add(ring2);

    // head
    const head = new THREE.Group(); head.position.y = 1.38; body.add(head);
    const skull = new THREE.Mesh(new THREE.SphereGeometry(0.8, 36, 28), white); skull.scale.set(1, 0.88, 0.92); head.add(skull);
    const visor = new THREE.Mesh(new THREE.SphereGeometry(0.66, 32, 24), dark); visor.scale.set(1, 0.62, 0.5); visor.position.set(0, 0.02, 0.44); head.add(visor);
    const eyes = [];
    for (const sx of [-1, 1]) {
      const e = new THREE.Mesh(new THREE.SphereGeometry(0.095, 16, 12), glow);
      e.scale.set(0.95, 1.35, 0.5); e.position.set(sx * 0.23, 0.05, 0.73); head.add(e); eyes.push(e);
    }
    for (const sx of [-1, 1]) {
      const ear = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.2, 0.16, 24), tint);
      ear.rotation.z = Math.PI / 2; ear.position.set(sx * 0.8, 0, 0); head.add(ear);
    }
    const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.34, 8), white); stem.position.y = 0.82; head.add(stem);
    const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.09, 16, 12), glow); bulb.position.y = 1.04; head.add(bulb);

    // arms (pivot at the shoulder)
    function arm(sx) {
      const p = new THREE.Group(); p.position.set(sx * 0.82, 0.4, 0);
      const upper = new THREE.Mesh(new THREE.CapsuleGeometry(0.14, 0.55, 6, 16), white); upper.position.y = -0.38; p.add(upper);
      const hand = new THREE.Mesh(new THREE.SphereGeometry(0.19, 18, 14), tint); hand.position.y = -0.86; p.add(hand);
      body.add(p); return p;
    }
    const armL = arm(-1), armR = arm(1);
    armL.rotation.z = 0.25; armR.rotation.z = -0.25;

    const bot = { group: g, body, head, armL, armR, eyes, flame, ring, ring2, bulb, phase: Math.random() * 6, wave: !!opts.wave, blink: 0 };
    return bot;
  }

  function animateBot(bot, t, a, mx, my) {
    const ph = bot.phase;
    bot.body.position.y = Math.sin(t * 1.5 + ph) * 0.13;
    bot.body.rotation.y = Math.sin(t * 0.6 + ph) * 0.08 + mx * 0.25 * a;
    bot.head.rotation.y = lerp(bot.head.rotation.y, mx * 0.55, 0.08);
    bot.head.rotation.x = lerp(bot.head.rotation.x, -my * 0.3, 0.08);
    const f = 1 + Math.sin(t * 18 + ph) * 0.12;
    bot.flame.scale.set(f, 0.8 + (a * 0.4) + Math.sin(t * 14 + ph) * 0.12, f);
    bot.ring.scale.setScalar(1 + Math.sin(t * 2 + ph) * 0.05);
    bot.ring2.scale.setScalar(1.35 + Math.sin(t * 2 + ph + 1) * 0.08);
    bot.bulb.scale.setScalar(1 + Math.sin(t * 5 + ph) * 0.25);
    // blink
    const b = (Math.sin(t * 0.9 + ph * 3) > 0.985) ? 0.1 : 1;
    for (const e of bot.eyes) e.scale.y = lerp(e.scale.y, 1.35 * b, 0.4);
    // arms
    if (bot.wave) {
      bot.armR.rotation.z = -2.5 + Math.sin(t * 5 + ph) * 0.35;
      bot.armL.rotation.z = 0.25 + Math.sin(t * 1.6 + ph) * 0.06;
    } else {
      bot.armR.rotation.z = -0.25 - Math.sin(t * 1.7 + ph) * 0.08 - a * 0.25;
      bot.armL.rotation.z = 0.25 + Math.sin(t * 1.7 + ph + 1) * 0.08 + a * 0.25;
    }
  }

  // ---- props ----------------------------------------------------------------------------------
  function envelopeMesh(color) {
    const g = new THREE.Group();
    const body = new THREE.Mesh(new THREE.BoxGeometry(0.64, 0.44, 0.05), new THREE.MeshStandardMaterial({ color, roughness: 0.4, emissive: color, emissiveIntensity: 0.12 }));
    g.add(body);
    const s = new THREE.Shape(); s.moveTo(-0.32, 0.22); s.lineTo(0.32, 0.22); s.lineTo(0, -0.04); s.closePath();
    const flap = new THREE.Mesh(new THREE.ShapeGeometry(s), new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.5, side: THREE.DoubleSide }));
    flap.position.z = 0.032; g.add(flap);
    return g;
  }
  const sheetTex = canvasTex(256, 340, (x, w, h) => {
    x.fillStyle = '#f8fafc'; rr(x, 0, 0, w, h, 18); x.fill();
    x.fillStyle = '#cbd5e1'; x.beginPath(); x.arc(52, 58, 28, 0, 7); x.fill();
    x.fillStyle = '#334155'; rr(x, 92, 40, 120, 14, 7); x.fill();
    x.fillStyle = '#94a3b8'; rr(x, 92, 66, 84, 10, 5); x.fill();
    x.fillStyle = '#6366f1'; rr(x, 24, 112, 80, 12, 6); x.fill();
    x.fillStyle = '#cbd5e1';
    for (let i = 0; i < 9; i++) { rr(x, 24, 142 + i * 20, i % 3 === 2 ? 130 : 208, 9, 4); x.fill(); }
  });
  function sheetMesh() { return new THREE.Mesh(new THREE.PlaneGeometry(0.9, 1.2), planeMat(sheetTex.tex)); }

  function calendarTex() {
    return canvasTex(320, 280, (x, w, h) => {
      x.fillStyle = 'rgba(12,18,44,.96)'; rr(x, 0, 0, w, h, 26); x.fill();
      x.strokeStyle = 'rgba(251,113,133,.9)'; x.lineWidth = 4; rr(x, 2, 2, w - 4, h - 4, 25); x.stroke();
      x.fillStyle = '#fb7185'; x.fillRect(0, 0, w, 52);
      x.fillStyle = '#fff'; x.font = '700 26px system-ui, sans-serif'; x.fillText('Interview', 22, 36);
      for (let r = 0; r < 3; r++) for (let c = 0; c < 6; c++) {
        x.fillStyle = (r === 1 && c === 3) ? '#fb7185' : 'rgba(165,176,214,.35)';
        x.beginPath(); x.arc(36 + c * 50, 94 + r * 38, 13, 0, 7); x.fill();
      }
      x.fillStyle = '#34d399'; rr(x, 22, 214, 118, 38, 19); x.fill();
      x.fillStyle = '#052e1f'; x.font = '700 20px system-ui, sans-serif'; x.fillText('Online', 50, 240);
      x.fillStyle = 'rgba(165,176,214,.28)'; rr(x, 156, 214, 130, 38, 19); x.fill();
      x.fillStyle = '#e5e9ff'; x.fillText('Offline', 190, 240);
    });
  }

  // each prop set returns { group, update(t, a) }
  function mailProps(d) {
    const group = new THREE.Group();
    const box = new THREE.Group(); box.position.set(d * 2.2, -0.6, 0.3); group.add(box);
    const boxBody = new THREE.Mesh(new THREE.BoxGeometry(1.35, 1.05, 0.9), new THREE.MeshStandardMaterial({ color: 0x1e3a8a, roughness: 0.35, metalness: 0.3, emissive: 0x38bdf8, emissiveIntensity: 0.18 }));
    box.add(boxBody);
    const slot = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.09, 0.05), new THREE.MeshBasicMaterial({ color: 0x07102b })); slot.position.set(0, 0.22, 0.46); box.add(slot);
    const lid = new THREE.Mesh(new THREE.BoxGeometry(1.45, 0.12, 1.0), new THREE.MeshStandardMaterial({ color: 0x38bdf8, roughness: 0.3, emissive: 0x38bdf8, emissiveIntensity: 0.3 })); lid.position.y = 0.58; box.add(lid);
    const mails = [];
    const colors = [0xea4335, 0x0a84ff, 0xea4335, 0x0a84ff, 0xfafafa, 0x0a84ff];
    colors.forEach((c, i) => { const m = envelopeMesh(c); m.userData = { u: i / colors.length, y: (Math.random() - 0.5) * 2.4, spin: (Math.random() - 0.5) * 3 }; group.add(m); mails.push(m); });
    return {
      group,
      update(t, a) {
        for (const m of mails) {
          const u = (m.userData.u + t * 0.18) % 1;
          const sx = d * 6.8, ex = d * 2.2;
          m.position.set(lerp(sx, ex, u), lerp(m.userData.y, -0.1, smooth(u)) + Math.sin(u * Math.PI) * 0.9, 0.5 + Math.sin(u * 6.28) * 0.3);
          m.rotation.set(0.3 + u * m.userData.spin, u * m.userData.spin * 2, u * 1.5);
          const s = (1 - smooth((u - 0.82) / 0.18)) * Math.min(1, u * 6) * (0.4 + 0.6 * a);
          m.scale.setScalar(Math.max(0.001, s) * 1.05);
        }
        box.rotation.y = Math.sin(t * 0.8) * 0.18; box.position.y = -0.6 + Math.sin(t * 1.3) * 0.08;
        lid.position.y = 0.58 + Math.max(0, Math.sin(t * 4)) * 0.05 * a;
      },
    };
  }

  function scoreProps(d) {
    const group = new THREE.Group();
    const gauge = new THREE.Group(); gauge.position.set(d * 2.7, 0.35, 0.4); group.add(gauge);
    const N = 54, R = 1.25;
    const segs = [];
    const lo = new THREE.Color(0xf87171), mid = new THREE.Color(0xfbbf24), hi = new THREE.Color(0x34d399);
    for (let i = 0; i < N; i++) {
      const f = i / (N - 1);
      const ang = Math.PI * 0.85 - f * Math.PI * 1.7; // sweep from lower-left, over the top, to lower-right
      const col = f < 0.5 ? lo.clone().lerp(mid, f * 2) : mid.clone().lerp(hi, (f - 0.5) * 2);
      const m = new THREE.Mesh(new THREE.BoxGeometry(0.11, 0.34, 0.14), new THREE.MeshBasicMaterial({ color: col, toneMapped: false, transparent: true }));
      m.position.set(Math.cos(ang) * R, Math.sin(ang) * R, 0); m.rotation.z = ang - Math.PI / 2 + Math.PI;
      m.userData.col = col; gauge.add(m); segs.push(m);
    }
    const disc = new THREE.Mesh(new THREE.CircleGeometry(1.0, 48), new THREE.MeshBasicMaterial({ color: 0x0a1030, transparent: true, opacity: 0.82 })); disc.position.z = -0.08; gauge.add(disc);
    const num = canvasTex(256, 160, () => {});
    const sprite = new THREE.Mesh(new THREE.PlaneGeometry(1.5, 0.94), planeMat(num.tex)); sprite.position.z = 0.05; gauge.add(sprite);
    let shown = -1;
    function setNum(n) {
      if (n === shown) return; shown = n;
      const x = num.ctx; x.clearRect(0, 0, 256, 160);
      x.fillStyle = '#fff'; x.font = '800 96px system-ui, sans-serif'; x.textAlign = 'center'; x.fillText(String(n), 112, 98);
      x.fillStyle = '#a5b0d6'; x.font = '600 26px system-ui, sans-serif'; x.textAlign = 'left'; x.fillText('/100', 168, 98);
      x.fillStyle = '#34d399'; x.font = '700 22px system-ui, sans-serif'; x.textAlign = 'center'; x.fillText('HIGH CONFIDENCE', 128, 136);
      num.tex.needsUpdate = true;
    }
    const sheets = [];
    for (let i = 0; i < 3; i++) { const s = sheetMesh(); s.userData.o = i * 2.09; group.add(s); sheets.push(s); }
    const chk = new THREE.Mesh(new THREE.CircleGeometry(0.17, 24), new THREE.MeshBasicMaterial({ color: 0x34d399 })); group.add(chk);
    return {
      group,
      update(t, a) {
        const target = 87 * smooth(a * 1.15);
        setNum(Math.round(target));
        const lit = (target / 100) * N;
        segs.forEach((s, i) => { const on = i < lit; s.material.opacity = on ? 1 : 0.16; s.scale.y = on ? 1 + Math.sin(t * 3 + i * 0.4) * 0.06 : 0.8; });
        gauge.rotation.y = Math.sin(t * 0.7) * 0.18; gauge.position.y = 0.35 + Math.sin(t * 1.2) * 0.08;
        sheets.forEach((s, i) => {
          const ang = t * 0.5 + s.userData.o;
          s.position.set(d * 2.7 + Math.cos(ang) * 2.5, 0.3 + Math.sin(ang * 1.3) * 0.8, -0.7 - (Math.sin(ang) + 1) * 0.55);
          s.rotation.set(0.1, -Math.cos(ang) * 0.35, Math.sin(t + i) * 0.1);
          if (i === 0) chk.position.set(s.position.x + 0.32, s.position.y - 0.45, s.position.z + 0.02), chk.rotation.copy(s.rotation);
        });
      },
    };
  }

  function jobCardTex(label, score, color, hot) {
    return canvasTex(420, 190, (x, w, h) => {
      x.fillStyle = 'rgba(10,16,42,.95)'; rr(x, 4, 4, w - 8, h - 8, 26); x.fill();
      x.strokeStyle = color; x.lineWidth = hot ? 6 : 3; rr(x, 4, 4, w - 8, h - 8, 26); x.stroke();
      x.fillStyle = '#a5b0d6'; x.font = '600 24px system-ui, sans-serif'; x.fillText(label, 30, 50);
      x.fillStyle = '#fff'; x.font = '800 86px system-ui, sans-serif'; x.fillText(String(score), 28, 142);
      x.fillStyle = 'rgba(165,176,214,.25)'; rr(x, 150, 108, 240, 16, 8); x.fill();
      x.fillStyle = color; rr(x, 150, 108, 240 * score / 100, 16, 8); x.fill();
    });
  }
  function matchProps(d) {
    const group = new THREE.Group();
    const cardA = new THREE.Mesh(new THREE.PlaneGeometry(2.5, 1.13), planeMat(jobCardTex('CURRENT JOB', 62, '#94a3b8', false).tex)); cardA.position.set(d * 2.9, 1.25, 0.2);
    const cardB = new THREE.Mesh(new THREE.PlaneGeometry(2.5, 1.13), planeMat(jobCardTex('BETTER FIT', 81, '#34d399', true).tex)); cardB.position.set(d * 2.9, -0.85, 0.5);
    group.add(cardA, cardB);
    const arrow = new THREE.Group(); arrow.position.set(d * 2.9, 0.2, 0.4); group.add(arrow);
    const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.5, 12), new THREE.MeshBasicMaterial({ color: 0x34d399 })); arrow.add(shaft);
    const tip = new THREE.Mesh(new THREE.ConeGeometry(0.18, 0.3, 16), new THREE.MeshBasicMaterial({ color: 0x34d399 })); tip.rotation.x = Math.PI; tip.position.y = -0.38; arrow.add(tip);
    const badge = canvasTex(220, 90, (x, w, h) => {
      x.fillStyle = '#34d399'; rr(x, 4, 4, w - 8, h - 8, 40); x.fill();
      x.fillStyle = '#052e1f'; x.font = '800 44px system-ui, sans-serif'; x.textAlign = 'center'; x.fillText('+19', w / 2, 62);
    });
    const badgeM = new THREE.Mesh(new THREE.PlaneGeometry(1.0, 0.41), planeMat(badge.tex)); group.add(badgeM);
    const star = new THREE.Mesh(new THREE.OctahedronGeometry(0.3), new THREE.MeshStandardMaterial({ color: 0xfbbf24, emissive: 0xfbbf24, emissiveIntensity: 0.7, roughness: 0.2, metalness: 0.5 })); group.add(star);
    const glowTex = canvasTex(256, 128, (x, w, h) => { const g = x.createRadialGradient(w / 2, h / 2, 4, w / 2, h / 2, w / 2); g.addColorStop(0, 'rgba(52,211,153,.9)'); g.addColorStop(1, 'rgba(52,211,153,0)'); x.fillStyle = g; x.fillRect(0, 0, w, h); });
    const glowRing = new THREE.Mesh(new THREE.PlaneGeometry(4.6, 2.5), planeMat(glowTex.tex, { blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.4 })); glowRing.position.copy(cardB.position); glowRing.position.z = 0.2; group.add(glowRing);
    return {
      group,
      update(t, a) {
        cardA.position.y = 1.25 + Math.sin(t * 1.1) * 0.07; cardA.rotation.y = d * -0.25 + Math.sin(t * 0.6) * 0.05;
        cardB.position.y = -0.85 + Math.sin(t * 1.3 + 1) * 0.08; cardB.rotation.y = d * -0.25 + Math.sin(t * 0.7) * 0.05;
        const pulse = (t * 1.2) % 1;
        arrow.position.y = 0.45 - pulse * 0.5; arrow.scale.setScalar(0.6 + a * 0.5);
        const pop = smooth((a - 0.45) / 0.4);
        star.position.set(cardB.position.x - d * 1.15, cardB.position.y + 0.95, 0.9); star.rotation.set(t, t * 1.3, 0); star.scale.setScalar(0.001 + pop * (1.5 + Math.sin(t * 4) * 0.12));
        badgeM.position.set(cardB.position.x + d * 0.95, cardB.position.y + 0.85, 0.7); badgeM.scale.setScalar(0.001 + pop);
        glowRing.position.y = cardB.position.y; glowRing.material.opacity = 0.15 + pop * 0.35 + Math.sin(t * 2) * 0.04;
      },
    };
  }

  function hostProps(d) {
    const group = new THREE.Group();
    const mon = new THREE.Group(); mon.position.set(d * 3.1, 0.55, 0.3); group.add(mon);
    const frame = new THREE.Mesh(new THREE.BoxGeometry(3.2, 2.05, 0.16), new THREE.MeshStandardMaterial({ color: 0x0a1030, roughness: 0.2, metalness: 0.7, emissive: 0xfb7185, emissiveIntensity: 0.12 })); mon.add(frame);
    const tiles = canvasTex(640, 400, (x, w, h) => {
      x.fillStyle = '#0b1230'; x.fillRect(0, 0, w, h);
      const cols = ['#38bdf8', '#a78bfa', '#34d399', '#fb7185'];
      for (let i = 0; i < 4; i++) {
        const px = 14 + (i % 2) * 308, py = 14 + Math.floor(i / 2) * 150;
        x.fillStyle = '#16204a'; rr(x, px, py, 296, 138, 14); x.fill();
        x.fillStyle = cols[i]; x.beginPath(); x.arc(px + 148, py + 56, 28, 0, 7); x.fill();
        x.beginPath(); x.ellipse(px + 148, py + 128, 52, 38, 0, Math.PI, 0); x.fill();
      }
      x.fillStyle = '#e11d48'; rr(x, 232, 322, 176, 52, 26); x.fill();
      x.fillStyle = '#fff'; x.font = '700 26px system-ui, sans-serif'; x.textAlign = 'center'; x.fillText('Teams  |  Meet', 320, 357);
      x.fillStyle = 'rgba(255,255,255,.18)'; for (const cx of [100, 160, 480, 540]) { x.beginPath(); x.arc(cx, 348, 22, 0, 7); x.fill(); }
    });
    const screen = new THREE.Mesh(new THREE.PlaneGeometry(3.0, 1.88), planeMat(tiles.tex)); screen.position.z = 0.09; mon.add(screen);
    const outlines = [];
    for (let i = 0; i < 4; i++) {
      const o = new THREE.Mesh(new THREE.PlaneGeometry(1.4, 0.64), new THREE.MeshBasicMaterial({ color: 0xfb7185, transparent: true, opacity: 0, toneMapped: false }));
      o.position.set(-0.72 + (i % 2) * 1.44, 0.46 - Math.floor(i / 2) * 0.7, 0.085); mon.add(o); outlines.push(o);
    }
    const stand = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.18, 0.55, 16), new THREE.MeshStandardMaterial({ color: 0x1e293b, metalness: 0.6, roughness: 0.3 })); stand.position.y = -1.3; mon.add(stand);
    const cal = calendarTex(); const calM = new THREE.Mesh(new THREE.PlaneGeometry(1.7, 1.49), planeMat(cal.tex)); group.add(calM);
    return {
      group,
      update(t, a) {
        mon.rotation.y = d * -0.22 + Math.sin(t * 0.6) * 0.06; mon.position.y = 0.55 + Math.sin(t * 1.1) * 0.07;
        const k = Math.floor(t * 0.8) % 4;
        outlines.forEach((o, i) => { o.material.opacity = (i === k ? 0.5 + Math.sin(t * 8) * 0.15 : 0.0) * a; });
        calM.position.set(d * 2.0, -1.75 + Math.sin(t * 1.4 + 2) * 0.08, 1.2); calM.rotation.y = d * -0.3 + Math.sin(t * 0.8) * 0.1; calM.rotation.z = Math.sin(t * 0.9) * 0.05;
      },
    };
  }

  function heroProps() {
    const group = new THREE.Group();
    const orb = [];
    const env = envelopeMesh(0x38bdf8); env.scale.setScalar(1.1);
    const doc = sheetMesh(); doc.scale.setScalar(0.85);
    const star = new THREE.Mesh(new THREE.OctahedronGeometry(0.34), new THREE.MeshStandardMaterial({ color: 0xfbbf24, emissive: 0xfbbf24, emissiveIntensity: 0.6, roughness: 0.2, metalness: 0.5 }));
    const calT = calendarTex(); const cal = new THREE.Mesh(new THREE.PlaneGeometry(1.1, 0.96), planeMat(calT.tex));
    [env, doc, star, cal].forEach((m, i) => { m.userData.o = i * (Math.PI / 2); m.userData.face = (m === doc || m === cal); group.add(m); orb.push(m); });
    return {
      group,
      update(t) {
        orb.forEach((m) => {
          const a = t * 0.55 + m.userData.o;
          m.position.set(Math.cos(a) * 3.3, Math.sin(a * 2) * 0.7 + 0.5, Math.sin(a) * 2.0);
          if (m.userData.face) { m.quaternion.copy(camera.quaternion); m.rotateZ(Math.sin(a * 2) * 0.15); }
          else m.rotation.set(Math.sin(a) * 0.4, -a * 0.4, Math.cos(a) * 0.2);
        });
        star.rotation.y = t * 2;
      },
    };
  }

  // ---- stages ---------------------------------------------------------------------------------
  const accents = [0x818cf8, 0x38bdf8, 0xa78bfa, 0x34d399, 0xfb7185, 0x818cf8];
  const sides = [1, 1, -1, 1, -1, 0];          // which side of the screen the character stands on
  const stageDefs = [
    { bot: { wave: true }, props: () => heroProps(), scale: 1.35 },
    { props: (d) => mailProps(d) },
    { props: (d) => scoreProps(d) },
    { props: (d) => matchProps(d) },
    { props: (d) => hostProps(d) },
    { team: true },
  ];

  const stageObjs = stageDefs.map((def, i) => {
    const g = new THREE.Group(); scene.add(g);
    const o = { group: g, side: sides[i], def, props: null, bots: [], baseScale: def.scale || 1 };
    if (def.team) {
      const xs = [-4.2, -1.4, 1.4, 4.2];
      [accents[1], accents[2], accents[3], accents[4]].forEach((c, k) => {
        const b = makeBot(c, { wave: true }); b.group.position.set(xs[k], 0, 0); b.group.scale.setScalar(0.62); b.phase = k * 1.3; g.add(b.group); o.bots.push(b);
      });
    } else {
      const b = makeBot(accents[i], def.bot || {}); g.add(b.group); o.bots.push(b);
      o.props = def.props(i === 0 ? 1 : -sides[i]);   // props sit between the character and the text card
      g.add(o.props.group);
    }
    return o;
  });

  // ---- background depth layers ----------------------------------------------------------------
  function starLayer(count, size, spreadY, zMin, zMax, color) {
    const pos = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      pos[i * 3] = (Math.random() - 0.5) * 70; pos[i * 3 + 1] = -spreadY + Math.random() * (spreadY + 14); pos[i * 3 + 2] = zMin + Math.random() * (zMax - zMin);
    }
    const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    return new THREE.Points(geo, new THREE.PointsMaterial({ color, size, sizeAttenuation: true, transparent: true, opacity: 0.9, depthWrite: false, blending: THREE.AdditiveBlending }));
  }
  const mobileLite = isCoarse || Math.min(innerWidth, innerHeight) < 700;
  const layers = [
    starLayer(mobileLite ? 260 : 520, 0.09, 62, -45, -12, 0xbfd4ff),
    starLayer(mobileLite ? 120 : 240, 0.16, 62, -14, -2, 0xa5b4fc),
    starLayer(mobileLite ? 30 : 70, 0.26, 62, 4, 9, 0xf0abfc),
  ];
  layers.forEach((l) => scene.add(l));

  const floaters = [];
  const fGeos = [new THREE.IcosahedronGeometry(1, 0), new THREE.TorusGeometry(0.8, 0.16, 10, 36), new THREE.OctahedronGeometry(1, 0), new THREE.BoxGeometry(1.2, 1.2, 1.2)];
  const fCols = [0x38bdf8, 0xa78bfa, 0x34d399, 0xfb7185, 0x818cf8];
  const FN = mobileLite ? 14 : 26;
  for (let i = 0; i < FN; i++) {
    const m = new THREE.Mesh(fGeos[i % fGeos.length], new THREE.MeshBasicMaterial({ color: fCols[i % fCols.length], wireframe: i % 2 === 0, transparent: true, opacity: i % 2 === 0 ? 0.35 : 0.12 }));
    const far = i % 3 !== 0;
    m.position.set((Math.random() - 0.5) * 34, -Math.random() * 56 + 4, far ? -6 - Math.random() * 22 : 5 + Math.random() * 3);
    m.scale.setScalar(far ? 0.8 + Math.random() * 1.8 : 0.5 + Math.random() * 0.7);
    m.userData = { r: (Math.random() - 0.5) * 0.6, s: 0.2 + Math.random() * 0.4, y0: m.position.y, k: Math.random() * 6 };
    scene.add(m); floaters.push(m);
  }

  // ---- layout / scroll mapping ----------------------------------------------------------------
  let W = 1, H = 1, portrait = false, centers = [];
  function measure() {
    W = innerWidth; H = innerHeight;
    renderer.setSize(W, H, false);
    camera.aspect = W / H; portrait = camera.aspect < 0.95;
    camera.position.z = portrait ? 17 : 14;
    camera.updateProjectionMatrix();
    const sy = window.scrollY;
    centers = stages.map((s) => s.getBoundingClientRect().top + sy + s.offsetHeight / 2 - H / 2);
    stageObjs.forEach((o, i) => {
      const x = (portrait || o.side === 0) ? 0 : o.side * 4.3;
      o.group.position.set(x, -i * SPACING + (o.def.team ? (portrait ? -3.4 : -3.0) : (portrait ? 2.6 : 0)), 0);
      o.group.scale.setScalar(portrait ? (o.def.team ? 0.6 : 0.62 * (o.baseScale > 1 ? 0.85 : 1)) : o.baseScale);
      if (o.def.team) { const xs = portrait ? [-1.9, -0.65, 0.65, 1.9] : [-4.2, -1.4, 1.4, 4.2]; o.bots.forEach((b, k) => b.group.position.x = xs[k] * (portrait ? 2.0 : 1)); }
    });
  }
  function scrollT() {
    const y = window.scrollY;
    if (y <= centers[0]) return 0;
    for (let i = 0; i < centers.length - 1; i++) {
      if (y < centers[i + 1]) return i + (y - centers[i]) / Math.max(1, centers[i + 1] - centers[i]);
    }
    return centers.length - 1;
  }
  addEventListener('resize', measure);
  addEventListener('load', measure);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(measure);
  if (window.ResizeObserver) new ResizeObserver(measure).observe(document.body);
  measure();

  // ---- input ----------------------------------------------------------------------------------
  let tx = 0, ty = 0, mx = 0, my = 0;
  addEventListener('pointermove', (e) => { tx = (e.clientX / innerWidth) * 2 - 1; ty = (e.clientY / innerHeight) * 2 - 1; }, { passive: true });

  // ---- loop -----------------------------------------------------------------------------------
  let camT = 0, last = performance.now(), running = true;
  document.addEventListener('visibilitychange', () => { running = !document.hidden; if (running) { last = performance.now(); requestAnimationFrame(frame); } });

  function frame(now) {
    if (!running) return;
    requestAnimationFrame(frame);
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    const t = now / 1000;
    mx = lerp(mx, tx, Math.min(1, dt * 4)); my = lerp(my, ty, Math.min(1, dt * 4));

    const target = scrollT();
    camT = lerp(camT, target, Math.min(1, dt * 5));
    const camY = -camT * SPACING;
    camera.position.set(mx * 0.9 + Math.sin(t * 0.15) * 0.15, camY - my * 0.5, camera.position.z);
    camera.lookAt(mx * 0.35, camY, 0);
    rig.position.y = camY;
    setActive(Math.max(0, Math.min(stages.length - 1, Math.round(camT))));

    layers.forEach((l, i) => { l.position.x = -mx * (0.4 + i * 0.7); l.position.y = my * 0.25 * (i + 1); l.rotation.y = t * 0.004 * (i + 1); });
    floaters.forEach((m) => { m.rotation.x += m.userData.r * dt; m.rotation.y += m.userData.s * dt; m.position.y = m.userData.y0 + Math.sin(t * 0.5 + m.userData.k) * 0.5; });

    stageObjs.forEach((o, i) => {
      const dist = Math.abs(camT - i);
      const a = smooth(1 - dist / 0.9);
      const vis = dist < 1.6;
      o.group.visible = vis;
      if (!vis) return;
      const s = lerp(0.82, 1, a);
      o.bots.forEach((b) => { b.group.scale.setScalar(o.def.team ? (portrait ? 0.62 : 0.62) * s : s); animateBot(b, t, a, mx, my); });
      if (o.props) o.props.update(t, a);
    });

    renderer.render(scene, camera);
  }
  requestAnimationFrame(frame);
  window.__sceneReady = true;
}
