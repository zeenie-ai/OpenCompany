/**
 * The 3D orb's scene and frame loop: a port of the design prototype's
 * `initScene` (design_handoff_opencompany_home, reference/OpenCompany
 * Home.dc.html; spec in orb/ORB.md). Loaded only through orb.ts.
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
  Color,
  ColorManagement,
  DirectionalLight,
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
  SRGBColorSpace,
  Scene,
  SphereGeometry,
  Sprite,
  SpriteMaterial,
  TorusGeometry,
  Vector3,
  WebGLRenderer,
} from 'three';
import { prefersReducedMotion } from '@/lib/useReducedMotion';
import { orbState } from './orb';

ColorManagement.enabled = false;

const PI = Math.PI;
const POINT = 0.8 * PI;

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
  const p1 = new PointLight(0xbd93f9, 2.4 * POINT, 20, 0);
  p1.position.set(-3, 1, 3);
  const p2 = new PointLight(0x8be9fd, 2.4 * POINT, 20, 0);
  p2.position.set(3, -1.5, 2.5);
  scene.add(amb, key, p1, p2);

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
  const glow = (color: number, size: number, opacity: number) => {
    const s = new Sprite(
      new SpriteMaterial({ map: glowTex, color, transparent: true, opacity, blending: AdditiveBlending, depthWrite: false }),
    );
    s.scale.setScalar(size);
    return s;
  };

  const orb = new Group();
  const tilt = new Group();
  orb.add(tilt);
  scene.add(orb);

  // Ring: vertex colours purple -> cyan by angle; the light theme's greys kept beside them.
  const rg = new TorusGeometry(1.2, 0.24, 48, 220);
  const pos = rg.attributes.position;
  const ringDark = new Float32Array(pos.count * 3);
  const ringLight = new Float32Array(pos.count * 3);
  const tmp = new Color();
  const pairs = [
    [new Color(0xbd93f9), new Color(0x8be9fd), ringDark],
    [new Color(0x1a1d21), new Color(0x3a3f47), ringLight],
  ] as const;
  for (let i = 0; i < pos.count; i++) {
    const t = (Math.sin(Math.atan2(pos.getY(i), pos.getX(i)) - 0.8) + 1) / 2;
    for (const [a, b, out] of pairs) {
      tmp.copy(a).lerp(b, t);
      out.set([tmp.r, tmp.g, tmp.b], i * 3);
    }
  }
  rg.setAttribute('color', new BufferAttribute(Float32Array.from(ringDark), 3));
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
  const ring = new Mesh(rg, ringMat);
  tilt.add(ring);
  const coreMat = new MeshPhysicalMaterial({ color: 0xf8f8f2, emissive: 0xf8f8f2, emissiveIntensity: 0.55, roughness: 0.3, clearcoat: 1 });
  const core = new Mesh(new SphereGeometry(0.38, 64, 64), coreMat);
  orb.add(core);
  const coreGlow = glow(0xf8f8f2, 2.2, 0.35);
  const ringGlow = glow(0xbd93f9, 5, 0.12);
  orb.add(coreGlow, ringGlow);

  const nodes = [0x50fa7b, 0xffb86c, 0xff79c6, 0xf1fa8c].map((col, i) => {
    const mat = new MeshPhysicalMaterial({ color: col, emissive: col, emissiveIntensity: 0.35, roughness: 0.25, clearcoat: 1 });
    const m = new Mesh(new SphereGeometry(0.2, 32, 32), mat);
    const halo = glow(col, 1.1, 0.5);
    m.add(halo);
    const lg = new BufferGeometry();
    lg.setAttribute('position', new BufferAttribute(new Float32Array(33 * 3), 3));
    const line = new Line(lg, new LineBasicMaterial({ color: 0x6272a4, transparent: true, opacity: 0.7 }));
    const pk = glow(col, 0.45, 0.9);
    orb.add(m, line, pk);
    const cd = new Color(col);
    const cl = new Color([0x1a1d21, 0x2b2f37][i % 2]);
    return { m, mat, halo, line, pk, cd, cl, phase: (i * PI) / 2 + PI / 4, r: 2.0 + (i % 2) * 0.15, off: Math.random() };
  });

  const N = 900;
  const pp = new Float32Array(N * 3);
  const pc = new Float32Array(N * 3);
  const pal = [0x8be9fd, 0xbd93f9, 0x6272a4, 0xff79c6].map((hex) => new Color(hex));
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
    lL: new Color(0x6b7280),
    eD: new Color(0x2a1d4a),
    black: new Color(0x000000),
    gD: new Color(0xf8f8f2),
    gL: new Color(0x1a1d21),
    cK: new Color(0x111317),
  };
  const bez = new QuadraticBezierCurve3(new Vector3(), new Vector3(), new Vector3());
  const vh = 2 * 9 * Math.tan((17.5 * PI) / 180);

  let host: HTMLElement | null = null;
  let size = { w: 1, h: 1 };
  const resize = () => {
    if (!host) return;
    const w = host.clientWidth || 1;
    const h = host.clientHeight || 1;
    R.setSize(w, h, false);
    cam.aspect = w / h;
    cam.updateProjectionMatrix();
    size = { w, h };
  };
  const ro = new ResizeObserver(resize);
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
  let placed = false;
  let rx = 0;
  let ry = 0;
  let lf = document.documentElement.classList.contains('dark') ? 0 : 1;
  let lfApplied = -1;
  let appliedLight: boolean | null = null;

  const loop = (now: number) => {
    raf = requestAnimationFrame(loop);
    if (!host) return;
    const dt = Math.min((now - last) / 1000, 0.05);
    last = now;
    const mot = prefersReducedMotion() ? 0.15 : 1;
    energy += (orbState.target - energy) * Math.min(1, dt * 3);
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
    tilt.rotation.x = 0.3 + Math.sin(t * 0.5) * 0.18;
    tilt.rotation.y = Math.cos(t * 0.4) * 0.22;
    ring.rotation.z += dt * mot * (0.25 + e * 2.4);
    core.scale.setScalar(1 + Math.sin(now * 0.004 * (1 + e * 2)) * 0.04 * (1 + e * 3));
    coreGlow.material.opacity = 0.25 + e * 0.5;
    coreGlow.scale.setScalar(2 + e * 1.6);
    ringGlow.material.opacity = 0.08 + e * 0.18;
    nodes.forEach((n, i) => {
      const a = n.phase + t * 0.5;
      n.m.position.set(Math.cos(a) * n.r, Math.sin(a) * n.r * 0.62, Math.sin(a + (i % 2 ? 0.4 : -0.4)) * 0.9);
      const p = n.m.position;
      bez.v1.set(p.x * 0.5 - p.y * 0.25, p.y * 0.5 + p.x * 0.25, p.z * 0.5 + 0.6);
      bez.v2.copy(p);
      const arr = n.line.geometry.attributes.position.array as Float32Array;
      bez.getPoints(32).forEach((v, j) => arr.set([v.x, v.y, v.z], j * 3));
      n.line.geometry.attributes.position.needsUpdate = true;
      n.line.material.opacity = 0.45 + e * 0.45;
      bez.getPoint((t * 0.9 + n.off) % 1, n.pk.position);
      n.pk.material.opacity = 0.3 + e * 0.7;
    });

    // Light theme: monochrome, blended over ~0.3 s.
    const light = !document.documentElement.classList.contains('dark');
    ptsMat.opacity += ((light ? 0.45 : 0.6) - ptsMat.opacity) * k;
    pts.rotation.y += dt * 0.02 * mot * (1 + e * 3);
    pts.rotation.x = rx * 0.3;
    lf += ((light ? 1 : 0) - lf) * Math.min(1, dt * 3.2);
    nodes.forEach((n) => n.line.material.color.copy(tc.lD).lerp(tc.lL, lf));
    ringMat.emissive.copy(tc.eD).lerp(tc.black, lf);
    coreGlow.material.color.copy(tc.gD).lerp(tc.gL, lf);
    ringMat.iridescence = 0.7 * (1 - lf);
    ringMat.metalness = 0.25 * (1 - lf);
    ringMat.emissiveIntensity = 0.5 - 0.2 * lf;
    coreMat.color.copy(tc.gD).lerp(tc.cK, lf);
    coreMat.emissive.copy(tc.gD).lerp(tc.black, lf);
    coreMat.emissiveIntensity = 0.55 - 0.3 * lf;
    if (Math.abs(lf - lfApplied) > 0.002) {
      lfApplied = lf;
      const ca = rg.attributes.color.array as Float32Array;
      for (let i = 0; i < ca.length; i++) ca[i] = ringDark[i] + (ringLight[i] - ringDark[i]) * lf;
      rg.attributes.color.needsUpdate = true;
      nodes.forEach((n) => {
        n.mat.color.copy(n.cd).lerp(n.cl, lf);
        n.mat.emissive.copy(n.cd).lerp(tc.black, lf);
        n.pk.material.color.copy(n.cd).lerp(tc.gL, lf);
      });
      const pa = pg.attributes.color.array as Float32Array;
      for (let i = 0; i < pa.length; i += 3) {
        pa[i] = ptDark[i] + (0.3 - ptDark[i]) * lf;
        pa[i + 1] = ptDark[i + 1] + (0.31 - ptDark[i + 1]) * lf;
        pa[i + 2] = ptDark[i + 2] + (0.33 - ptDark[i + 2]) * lf;
      }
      pg.attributes.color.needsUpdate = true;
    }
    ringMat.clearcoat = 1 - 0.6 * lf;
    ringMat.roughness = 0.18 + 0.2 * lf;
    nodes.forEach((n) => {
      n.mat.emissiveIntensity = 0.35 - 0.2 * lf;
      n.mat.clearcoat = 1 - 0.6 * lf;
      n.halo.material.opacity = 0.5 - 0.3 * lf;
    });
    ringGlow.material.opacity *= 1 - 0.8 * lf;
    coreGlow.material.opacity *= 1 - 0.9 * lf;
    R.toneMappingExposure = 1.1 - 0.1 * lf;
    amb.intensity = (0.35 + 0.2 * lf) * PI;
    key.intensity = (1.1 - 0.4 * lf) * PI;
    p1.intensity = p2.intensity = (2.4 - 1.2 * lf) * POINT;
    const wantLight = lf > 0.5;
    if (appliedLight !== wantLight) {
      appliedLight = wantLight;
      ptsMat.blending = ringGlow.material.blending = wantLight ? NormalBlending : AdditiveBlending;
      ptsMat.needsUpdate = ringGlow.material.needsUpdate = true;
    }
    R.render(scene, cam);
  };

  const stop = () => {
    cancelAnimationFrame(raf);
    raf = 0;
  };

  return {
    attach(element: HTMLElement) {
      host = element;
      element.appendChild(R.domElement);
      ro.disconnect();
      ro.observe(element);
      resize();
      placed = false;
      window.addEventListener('pointermove', onMove, { passive: true });
      if (!raf) {
        last = performance.now();
        raf = requestAnimationFrame(loop);
      }
    },
    detach() {
      stop();
      ro.disconnect();
      window.removeEventListener('pointermove', onMove);
      R.domElement.remove();
      host = null;
    },
    dispose() {
      this.detach();
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
