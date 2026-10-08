/**
 * The 3D orb's scene and frame loop: a port of the design prototype's
 * `initScene` (the Home handoff's reference page), with the onboarding
 * handoff's waiting mode and sizing fixes (its oc-orb.js). Loaded only
 * through orb.ts.
 *
 * Its shapes are the logo's mark in 3D (brand/geometry.ts): the C, a ring
 * open on one side, around the core, and the council, three heads each
 * trailing a crescent that tapers along the ring. The ring spins, the
 * council turns more slowly the same way (heads first), and a line from the
 * core to each head carries a packet. It keeps its own colours.
 *
 * While the server can't be reached (`orbState.waiting`) it eases into a
 * waiting mode over about a second: slower, a double heartbeat in the core,
 * the council breathing out and back in turn, one searching ping per line,
 * a little dimmer. It eases back the same way.
 *
 * The host is measured every frame and the drawing buffer follows it;
 * nothing is drawn until the host has a size, so a first 0x0 measure never
 * leaves a 1x1 buffer. Frames stop while the page is hidden or the window
 * is blurred (lib/pageActivity).
 *
 * Changes from the prototype's three r149 for current three:
 * - colour management off and sRGB output, so colours read as authored;
 * - lights are physically based now: intensities x PI, and the point
 *   lights keep the brightness the old linear falloff gave at the orb
 *   (about 80% at ~4 units) with no distance decay;
 * - no preserveDrawingBuffer.
 */

import {
  ACESFilmicToneMapping,
  AdditiveBlending,
  AmbientLight,
  BufferAttribute,
  BufferGeometry,
  CanvasTexture,
  CircleGeometry,
  Color,
  ColorManagement,
  DirectionalLight,
  Float32BufferAttribute,
  Group,
  Line,
  LineBasicMaterial,
  Mesh,
  MeshPhysicalMaterial,
  NormalBlending,
  PerspectiveCamera,
  PointLight,
  Points,
  PointsMaterial,
  QuadraticBezierCurve3,
  Quaternion,
  SRGBColorSpace,
  Scene,
  SphereGeometry,
  Sprite,
  SpriteMaterial,
  TorusGeometry,
  Vector3,
  WebGLRenderer,
} from 'three';
import { pageActivity } from '@/lib/pageActivity';
import { prefersReducedMotion } from '@/lib/useReducedMotion';
import { ENERGY, orbState } from './orb';

ColorManagement.enabled = false;

const PI = Math.PI;
const POINT = 0.8 * PI;

/** The mark in world units (its ring's outer edge at 1.44). */
const RING_R = 1.2;
const TUBE = 0.24;
const GAP = (12.5 * PI) / 180;
const CORE_R = 0.46;
const HEAD_R = 0.3;
const HEAD_D = 1.916;
/** A crescent runs from 16 to 37 degrees clockwise of its head: its centre
 *  line closes in on the ring and its thickness falls to a point, the mark's
 *  profile. It starts later than in the mark, so the round tube clears the
 *  head, and rounds off over its first 4 degrees. */
const CRESCENT = { from: (16 * PI) / 180, to: (37 * PI) / 180, dome: 4 / 21, r0: 1.83, dr: 0.28, thick: 0.26 };

/** Waiting: the heartbeat's period (s), and how far a member breathes out
 *  (a share of its distance from the core). */
const BEAT_S = 1.8;
const BREATH = 0.11;

/** The C's open end: a flat disc across the tube at `angle`, facing out of it. */
function ringCap(angle: number, facing: 1 | -1): BufferGeometry {
  const out = new Vector3(Math.sin(angle), -Math.cos(angle), 0).multiplyScalar(facing);
  return new CircleGeometry(TUBE, 48)
    .applyQuaternion(new Quaternion().setFromUnitVectors(new Vector3(0, 0, 1), out))
    .translate(Math.cos(angle) * RING_R, Math.sin(angle) * RING_R, 0);
}

/** A member's crescent: a tube sweeping clockwise from the head at
 *  `headAngle`, closed at both ends. */
function crescentGeometry(headAngle: number): BufferGeometry {
  const ALONG = 48;
  const AROUND = 20;
  const { from, to, dome, r0, dr, thick } = CRESCENT;
  const at = (f: number, out: Vector3) => {
    const a = headAngle - (from + (to - from) * f);
    const r = r0 - dr * f ** 1.5;
    return out.set(Math.cos(a) * r, Math.sin(a) * r, 0);
  };
  const centre = new Vector3();
  const ahead = new Vector3();
  const side = new Vector3();
  const positions: number[] = [];
  for (let i = 0; i <= ALONG; i++) {
    const f = i / ALONG;
    at(f, centre);
    // Along the tube (clockwise); `side` is the outward normal in the ring's plane.
    const step = 1 / ALONG / 2;
    at(Math.min(1, f + step), ahead).sub(at(Math.max(0, f - step), side));
    side.set(-ahead.y, ahead.x, 0).normalize();
    const d = Math.min(1, f / dome);
    const h = thick * (1 - f) * Math.sqrt(1 - (1 - d) ** 2);
    for (let j = 0; j < AROUND; j++) {
      const psi = (j / AROUND) * 2 * PI;
      positions.push(centre.x + side.x * h * Math.cos(psi), centre.y + side.y * h * Math.cos(psi), h * Math.sin(psi));
    }
  }
  const index: number[] = [];
  for (let i = 0; i < ALONG; i++) {
    for (let j = 0; j < AROUND; j++) {
      const a = i * AROUND + j;
      const b = i * AROUND + ((j + 1) % AROUND);
      index.push(a, b, a + AROUND, b, b + AROUND, a + AROUND);
    }
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new Float32BufferAttribute(positions, 3));
  geometry.setIndex(index);
  geometry.computeVertexNormals();
  return geometry;
}

export function createOrbEngine(onLost: () => void) {
  const R = new WebGLRenderer({ antialias: true, alpha: true });
  R.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  R.outputColorSpace = SRGBColorSpace;
  R.toneMapping = ACESFilmicToneMapping;
  R.toneMappingExposure = 1.1;
  R.domElement.style.cssText = 'position:absolute;inset:0;width:100%;height:100%';
  const lost = (event: Event) => {
    event.preventDefault();
    onLost();
  };
  R.domElement.addEventListener('webglcontextlost', lost);

  const scene = new Scene();
  const cam = new PerspectiveCamera(35, 1, 0.1, 100);
  cam.position.z = 9;
  const amb = new AmbientLight(0xffffff, 0.35 * PI);
  const key = new DirectionalLight(0xffffff, 1.1 * PI);
  key.position.set(3, 4, 5);
  // Rim light: off in dark, it puts the white highlights on the light theme's black.
  const rim = new DirectionalLight(0xffffff, 0);
  rim.position.set(-4, 3, -2);
  const p1 = new PointLight(0xbd93f9, 2.4 * POINT, 20, 0);
  p1.position.set(-3, 1, 3);
  const p2 = new PointLight(0x8be9fd, 2.4 * POINT, 20, 0);
  p2.position.set(3, -1.5, 2.5);
  scene.add(amb, rim, key, p1, p2);

  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d');
  if (g) {
    const gr = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    gr.addColorStop(0, 'rgba(255,255,255,1)');
    gr.addColorStop(0.25, 'rgba(255,255,255,0.4)');
    gr.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = gr;
    g.fillRect(0, 0, 128, 128);
  }
  const glowTex = new CanvasTexture(c);
  const sprites: Sprite[] = [];
  const glow = (color: number, size: number, opacity: number) => {
    const s = new Sprite(
      new SpriteMaterial({ map: glowTex, color, transparent: true, opacity, blending: AdditiveBlending, depthWrite: false }),
    );
    s.scale.setScalar(size);
    sprites.push(s);
    return s;
  };

  const orb = new Group();
  const tilt = new Group();
  orb.add(tilt);
  scene.add(orb);

  // The C: a torus open GAP either side of +x, with flat caps on its ends.
  // Vertex colours purple -> cyan by angle; the light theme's near-blacks kept beside them.
  const tmp = new Color();
  const ringStops = [
    [new Color(0xbd93f9), new Color(0x8be9fd)],
    [new Color(0x050506), new Color(0x16181c)],
  ] as const;
  const ringParts = [
    new TorusGeometry(RING_R, TUBE, 48, 200, 2 * PI - 2 * GAP).rotateZ(GAP),
    ringCap(GAP, 1),
    ringCap(2 * PI - GAP, -1),
  ].map((geometry) => {
    const pos = geometry.attributes.position;
    const dark = new Float32Array(pos.count * 3);
    const light = new Float32Array(pos.count * 3);
    for (let i = 0; i < pos.count; i++) {
      const t = (Math.sin(Math.atan2(pos.getY(i), pos.getX(i)) - 0.8) + 1) / 2;
      ringStops.forEach(([a, b], k) => {
        tmp.copy(a).lerp(b, t);
        (k ? light : dark).set([tmp.r, tmp.g, tmp.b], i * 3);
      });
    }
    geometry.setAttribute('color', new BufferAttribute(Float32Array.from(dark), 3));
    return { geometry, dark, light };
  });
  const ringMat = new MeshPhysicalMaterial({
    vertexColors: true,
    metalness: 0.25,
    roughness: 0.18,
    clearcoat: 1,
    clearcoatRoughness: 0.1,
    iridescence: 0.7,
    emissive: 0x2a1d4a,
    emissiveIntensity: 0.5,
  });
  const ring = new Group();
  ringParts.forEach(({ geometry }) => ring.add(new Mesh(geometry, ringMat)));
  tilt.add(ring);
  const coreMat = new MeshPhysicalMaterial({ color: 0xf8f8f2, emissive: 0xf8f8f2, emissiveIntensity: 0.55, roughness: 0.3, clearcoat: 1 });
  const core = new Mesh(new SphereGeometry(CORE_R, 64, 64), coreMat);
  orb.add(core);
  const coreGlow = glow(0xf8f8f2, 2.2, 0.35);
  const ringGlow = glow(0xbd93f9, 5, 0.12);
  orb.add(coreGlow, ringGlow);

  // The council: a head and its crescent every 120 degrees from the top, one
  // material each, grouped so a member can breathe out as one. The lines and
  // packets sit beside it in the tilted plane.
  const council = new Group();
  tilt.add(council);
  const members = [0xff79c6, 0xf1fa8c, 0x50fa7b].map((col, i) => {
    const angle = PI / 2 + (i * 2 * PI) / 3;
    const mat = new MeshPhysicalMaterial({ color: col, emissive: col, emissiveIntensity: 0.35, roughness: 0.25, clearcoat: 1 });
    const head = new Mesh(new SphereGeometry(HEAD_R, 32, 32), mat);
    head.position.set(Math.cos(angle) * HEAD_D, Math.sin(angle) * HEAD_D, 0);
    const halo = glow(col, 1.2, 0.5);
    head.add(halo);
    const member = new Group();
    member.add(head, new Mesh(crescentGeometry(angle), mat));
    council.add(member);
    const lg = new BufferGeometry();
    lg.setAttribute('position', new BufferAttribute(new Float32Array(33 * 3), 3));
    const line = new Line(lg, new LineBasicMaterial({ color: 0x6272a4, transparent: true, opacity: 0.7 }));
    const pk = glow(col, 0.45, 0.9);
    tilt.add(line, pk);
    const cd = new Color(col);
    const cl = new Color([0x060607, 0x0e0f12][i % 2]);
    return { angle, member, mat, halo, line, pk, cd, cl, off: Math.random() };
  });

  const N = 900;
  const pp = new Float32Array(N * 3);
  const pc = new Float32Array(N * 3);
  const pal = [0xffffff, 0xf8f8f2, 0xe8eaed, 0xffffff].map((hex) => new Color(hex));
  for (let i = 0; i < N; i++) {
    const r = 3 + Math.random() * 6;
    const th = Math.random() * PI * 2;
    const ph = Math.acos(2 * Math.random() - 1);
    pp.set([r * Math.sin(ph) * Math.cos(th) * 1.6, r * Math.cos(ph), r * Math.sin(ph) * Math.sin(th) - 2], i * 3);
    const cc = pal[i % 4];
    pc.set([cc.r, cc.g, cc.b], i * 3);
  }
  const ptDark = Float32Array.from(pc);
  const pg = new BufferGeometry();
  pg.setAttribute('position', new BufferAttribute(pp, 3));
  pg.setAttribute('color', new BufferAttribute(pc, 3));
  const ptsMat = new PointsMaterial({
    size: 0.07,
    map: glowTex,
    vertexColors: true,
    transparent: true,
    opacity: 0.6,
    blending: AdditiveBlending,
    depthWrite: false,
  });
  const pts = new Points(pg, ptsMat);
  scene.add(pts);

  const tc = {
    lD: new Color(0x6272a4),
    lL: new Color(0x1a1d21),
    eD: new Color(0x2a1d4a),
    black: new Color(0x000000),
    white: new Color(0xffffff),
    gD: new Color(0xf8f8f2),
    cK: new Color(0x030303),
    p1: new Color(0xbd93f9),
    p2: new Color(0x8be9fd),
  };
  const bez = new QuadraticBezierCurve3(new Vector3(), new Vector3(), new Vector3());
  const ping = new Vector3();
  const vh = 2 * 9 * Math.tan((17.5 * PI) / 180);

  let host: HTMLElement | null = null;
  /** The host's size in CSS pixels; null until it has one. */
  let size: { w: number; h: number } | null = null;
  const measure = (element: HTMLElement) => {
    const w = element.clientWidth;
    const h = element.clientHeight;
    if (w <= 0 || h <= 0 || (size && size.w === w && size.h === h)) return;
    R.setSize(w, h, false);
    cam.aspect = w / h;
    cam.updateProjectionMatrix();
    size = { w, h };
  };
  let mx = 0;
  let my = 0;
  const onMove = (e: PointerEvent) => {
    mx = (e.clientX / window.innerWidth) * 2 - 1;
    my = (e.clientY / window.innerHeight) * 2 - 1;
  };

  let raf = 0;
  let last = 0;
  let t = 0;
  let energy = orbState.target;
  /** How far into the waiting mode, 0..1. */
  let wf = orbState.waiting ? 1 : 0;
  let placed = false;
  let rx = 0;
  let ry = 0;
  let lf = document.documentElement.classList.contains('dark') ? 0 : 1;
  let lfApplied = -1;
  let appliedLight: boolean | null = null;
  let ptBase = 0.6;

  const loop = (now: number) => {
    raf = requestAnimationFrame(loop);
    if (!host) return;
    measure(host);
    if (!size) return;
    const dt = Math.min((now - last) / 1000, 0.05);
    last = now;
    const mot = prefersReducedMotion() ? 0.15 : 1;
    wf += ((orbState.waiting ? 1 : 0) - wf) * Math.min(1, dt * 1.6);
    energy += (orbState.target * (1 - wf) + ENERGY.waiting * wf - energy) * Math.min(1, dt * 3);
    orbState.spike = Math.max(0, orbState.spike - dt * 0.6);
    const e = Math.max(energy, orbState.spike);
    t += dt * mot * (0.4 + e * 1.8);

    // Glide to the slot.
    const { w, h } = size;
    const px = vh / h;
    let tx = orb.position.x;
    let ty = orb.position.y;
    let ts = orb.scale.x;
    const sl = orbState.slot;
    if (sl && sl.isConnected) {
      const hr = host.getBoundingClientRect();
      const r = sl.getBoundingClientRect();
      tx = (r.left + r.width / 2 - hr.left - w / 2) * px;
      ty = -(r.top + r.height / 2 - hr.top - h / 2) * px;
      ts = ((Math.min(r.width, r.height) / 2) * px) / 2.3;
    }
    if (!placed) {
      orb.position.set(tx, ty, 0);
      orb.scale.setScalar(ts);
      placed = true;
    }
    const k = Math.min(1, dt * 5);
    orb.position.x += (tx - orb.position.x) * k;
    orb.position.y += (ty - orb.position.y) * k;
    orb.scale.setScalar(orb.scale.x + (ts - orb.scale.x) * k);
    rx += (my * 0.25 - rx) * k;
    ry += (mx * 0.4 - ry) * k;
    orb.rotation.set(rx, ry, 0);
    tilt.rotation.x = 0.3 + Math.sin(t * 0.5) * 0.18 + wf * Math.sin(now * 0.0007) * 0.12;
    tilt.rotation.y = Math.cos(t * 0.4) * 0.22;
    ring.rotation.z += dt * mot * (0.25 + e * 2.4) * (1 - 0.45 * wf);
    council.rotation.z += dt * mot * (0.12 + e * 0.5) * (1 - 0.3 * wf);

    // The core pulses with the energy; waiting, a double heartbeat instead.
    const beatT = (now * 0.001) % BEAT_S;
    const beat = Math.exp(-((beatT - 0.1) ** 2) / 0.004) + 0.6 * Math.exp(-((beatT - 0.38) ** 2) / 0.004);
    const pulse = 1 + Math.sin(now * 0.004 * (1 + e * 2)) * 0.04 * (1 + e * 3);
    core.scale.setScalar(pulse * (1 - wf) + (1 + beat * 0.09) * wf);
    coreGlow.material.opacity = (0.25 + e * 0.5) * (1 - wf) + (0.22 + beat * 0.45) * wf;
    coreGlow.scale.setScalar((2 + e * 1.6) * (1 - wf) + (2 + beat * 1.4) * wf);
    ringGlow.material.opacity = 0.08 + e * 0.18;
    members.forEach((n, i) => {
      // Waiting, each member breathes out and back, in turn.
      const breath = wf * BREATH * Math.max(0, Math.sin(now * 0.0014 - (i * 2 * PI) / 3));
      n.member.position.set(Math.cos(n.angle) * HEAD_D * breath, Math.sin(n.angle) * HEAD_D * breath, 0);
      const a = n.angle + council.rotation.z;
      const d = HEAD_D * (1 + breath);
      const p = bez.v2.set(Math.cos(a) * d, Math.sin(a) * d, 0);
      bez.v1.set(p.x * 0.5 - p.y * 0.25, p.y * 0.5 + p.x * 0.25, 0.6);
      const arr = n.line.geometry.attributes.position.array as Float32Array;
      bez.getPoints(32).forEach((v, j) => arr.set([v.x, v.y, v.z], j * 3));
      n.line.geometry.attributes.position.needsUpdate = true;
      n.line.material.opacity = (0.45 + e * 0.45) * (1 - wf) + (0.18 + 0.12 * Math.sin(now * 0.003 + i)) * wf;
      // Packets stream from the core; waiting, one searching ping goes out
      // and fades. The two blend, so nothing jumps on the way in or out.
      const search = (now * 0.00045 + i / 3) % 1;
      const out = Math.min(1, search / 0.55);
      bez.getPoint((t * 0.9 + n.off) % 1, n.pk.position);
      n.pk.position.lerp(bez.getPoint(out, ping), wf);
      n.pk.material.opacity = (0.3 + e * 0.7) * (1 - wf) + (search < 0.55 ? Math.sin(PI * out) * 0.9 : 0) * wf;
    });

    // Light theme: glossy piano-black, blended over ~0.3 s.
    const light = !document.documentElement.classList.contains('dark');
    ptBase += ((light ? 0.85 : 0.8) * (1 - 0.35 * wf) - ptBase) * k;
    pts.rotation.y += dt * 0.02 * mot * (1 + e * 3);
    pts.rotation.x = rx * 0.3;
    lf += ((light ? 1 : 0) - lf) * Math.min(1, dt * 3.2);
    members.forEach((n) => n.line.material.color.copy(tc.lD).lerp(tc.lL, lf));
    ringMat.emissive.copy(tc.eD).lerp(tc.black, lf);
    coreGlow.material.color.copy(tc.gD).lerp(tc.black, lf);
    ringMat.iridescence = 0.7 * (1 - lf);
    ringMat.metalness = 0.25 * (1 - lf);
    ringMat.emissiveIntensity = 0.5 - 0.2 * lf;
    coreMat.color.copy(tc.gD).lerp(tc.cK, lf);
    coreMat.emissive.copy(tc.gD).lerp(tc.black, lf);
    coreMat.emissiveIntensity = 0.55 - 0.3 * lf;
    if (Math.abs(lf - lfApplied) > 0.002) {
      lfApplied = lf;
      for (const { geometry, dark, light: lit } of ringParts) {
        const ca = geometry.attributes.color.array as Float32Array;
        for (let i = 0; i < ca.length; i++) ca[i] = dark[i] + (lit[i] - dark[i]) * lf;
        geometry.attributes.color.needsUpdate = true;
      }
      members.forEach((n) => {
        n.mat.color.copy(n.cd).lerp(n.cl, lf);
        n.mat.emissive.copy(n.cd).lerp(tc.black, lf);
        n.pk.material.color.setScalar(1 - lf);
        n.halo.material.color.copy(n.mat.color);
      });
      const pa = pg.attributes.color.array as Float32Array;
      for (let i = 0; i < pa.length; i += 3) {
        pa[i] = ptDark[i] - ptDark[i] * lf;
        pa[i + 1] = ptDark[i + 1] - ptDark[i + 1] * lf;
        pa[i + 2] = ptDark[i + 2] + (0.01 - ptDark[i + 2]) * lf;
      }
      pg.attributes.color.needsUpdate = true;
    }
    ringMat.clearcoatRoughness = 0.1 - 0.06 * lf;
    ringMat.roughness = 0.18 - 0.08 * lf;
    coreMat.roughness = 0.3 - 0.2 * lf;
    members.forEach((n) => {
      n.mat.emissiveIntensity = 0.35 * (1 - lf) * (1 - 0.4 * wf);
      n.mat.roughness = 0.25 - 0.15 * lf;
      n.halo.material.opacity = (0.5 - 0.35 * lf) * (1 - 0.5 * wf);
    });
    R.toneMappingExposure = (1.1 - 0.2 * lf) * (1 - 0.12 * wf);
    amb.intensity = (0.35 - 0.2 * lf) * PI;
    key.intensity = (1.1 + 1.4 * lf) * PI;
    rim.intensity = 2.2 * lf * PI;
    p1.intensity = p2.intensity = (2.4 - 1.2 * lf) * POINT;
    p1.color.copy(tc.p1).lerp(tc.white, lf);
    p2.color.copy(tc.p2).lerp(tc.white, lf);

    // Additive glow vanishes on white, so light uses normal blending. The swap
    // happens at lf .5, where sprites and particles fade through zero. Runs
    // last: every opacity above is reset each frame before this scales it.
    const wantLight = lf > 0.5;
    if (appliedLight !== wantLight) {
      appliedLight = wantLight;
      for (const material of [ptsMat, ...sprites.map((sprite) => sprite.material)]) {
        material.blending = wantLight ? NormalBlending : AdditiveBlending;
        material.needsUpdate = true;
      }
    }
    const xf = Math.min(1, Math.abs(lf - 0.5) * 4);
    ptsMat.opacity = ptBase * xf;
    ptsMat.size = 0.08 + 0.02 * lf;
    const sk = xf * (wantLight ? 0.5 : 1);
    for (const sprite of sprites) sprite.material.opacity *= sk;
    R.render(scene, cam);
  };

  const stop = () => {
    cancelAnimationFrame(raf);
    raf = 0;
  };
  /** Frames run while there is a host and someone can see the page. */
  const run = () => {
    if (!host || !pageActivity.isActive()) {
      stop();
      return;
    }
    if (!raf) {
      last = performance.now();
      raf = requestAnimationFrame(loop);
    }
  };
  const unsubscribe = pageActivity.subscribe(run);

  return {
    attach(element: HTMLElement) {
      host = element;
      element.appendChild(R.domElement);
      size = null;
      placed = false;
      window.addEventListener('pointermove', onMove, { passive: true });
      run();
    },
    detach() {
      stop();
      window.removeEventListener('pointermove', onMove);
      R.domElement.remove();
      host = null;
      size = null;
    },
    dispose() {
      this.detach();
      unsubscribe();
      R.domElement.removeEventListener('webglcontextlost', lost);
      scene.traverse((object) => {
        const mesh = object as Mesh;
        mesh.geometry?.dispose();
        (mesh.material as { dispose?: () => void } | undefined)?.dispose?.();
      });
      glowTex.dispose();
      R.dispose();
    },
  };
}
