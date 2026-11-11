// Liquid chrome background: one fragment shader at half resolution. It renders only while
// something changes (pointer movement, theme, intensity, resize); when idle it draws nothing.
import { THEME_EVENT, currentIntensity, isLight } from "../theme";

const VS = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
const FS = `precision mediump float;
uniform vec2 res,mouse;uniform float t,amp,inten,light;
float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y);}
float fbm(vec2 p){float v=0.,a=.5;for(int i=0;i<4;i++){v+=a*n(p);p*=2.03;a*=.5;}return v;}
float field(vec2 uv){float d=distance(uv,mouse);float ripple=amp*exp(-d*5.)*sin(d*38.-t*5.);
 return fbm(uv*vec2(.85,1.35)+vec2(0.,t*.015))*.9+ripple*.22+(1.-d)*.12;}
void main(){vec2 uv=gl_FragCoord.xy/res;uv.x*=res.x/res.y;
 float e=2./res.y;float c=field(uv);vec3 nr=normalize(vec3(field(uv+vec2(e,0.))-c,field(uv+vec2(0.,e))-c,e*1.4));
 float band=.5+.5*sin(nr.y*5.+nr.x*2.5+c*4.);
 float spec=pow(max(0.,dot(nr,normalize(vec3(mouse.x-uv.x,mouse.y-uv.y,.9)))),24.);
 float metal=band*.55+spec*.9;
 vec3 dark=vec3(.02,.022,.026)+vec3(.11,.115,.125)*metal*(.3+inten*.6)+vec3(.30,.55,.75)*spec*.06*inten;
 vec3 lite=vec3(.85,.855,.85)+vec3(.12)*inten-vec3(.09,.09,.085)*(1.-metal)*(.4+.3*(1.-inten));
 gl_FragColor=vec4(mix(dark,lite,light),1.);}`;

export function startLiquid(cv: HTMLCanvasElement, animate: boolean): boolean {
  const g = cv.getContext("webgl", { antialias: false, alpha: false, powerPreference: "low-power" });
  if (!g) return false;
  const sh = (type: number, src: string) => { const s = g.createShader(type)!; g.shaderSource(s, src); g.compileShader(s); return s; };
  const pr = g.createProgram()!;
  g.attachShader(pr, sh(g.VERTEX_SHADER, VS)); g.attachShader(pr, sh(g.FRAGMENT_SHADER, FS)); g.linkProgram(pr);
  if (!g.getProgramParameter(pr, g.LINK_STATUS)) return false;
  g.useProgram(pr);
  g.bindBuffer(g.ARRAY_BUFFER, g.createBuffer());
  g.bufferData(g.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), g.STATIC_DRAW);
  const loc = g.getAttribLocation(pr, "p"); g.enableVertexAttribArray(loc); g.vertexAttribPointer(loc, 2, g.FLOAT, false, 0, 0);
  const U = Object.fromEntries(["res", "mouse", "t", "amp", "inten", "light"].map(k => [k, g.getUniformLocation(pr, k)]));

  const scale = 0.5;
  let amp = 0, t = 0, last = 0, running = false;
  const m = [0.7, 0.6], target = [0.7, 0.6];
  const size = () => {
    cv.width = Math.ceil(innerWidth * scale); cv.height = Math.ceil(innerHeight * scale);
    g.viewport(0, 0, cv.width, cv.height);
  };
  const render = () => {
    g.uniform2f(U.res, cv.width, cv.height); g.uniform2f(U.mouse, m[0] * cv.width / cv.height, m[1]);
    g.uniform1f(U.t, t); g.uniform1f(U.amp, amp); g.uniform1f(U.inten, currentIntensity()); g.uniform1f(U.light, isLight() ? 1 : 0);
    g.drawArrays(g.TRIANGLE_STRIP, 0, 4);
  };
  const tick = (now: number) => {
    t += Math.min(0.05, (now - last) / 1000 || 0.016); last = now;
    m[0] += (target[0] - m[0]) * 0.08; m[1] += (target[1] - m[1]) * 0.08; amp *= 0.965;
    render();
    if (amp > 0.01 || Math.abs(target[0] - m[0]) + Math.abs(target[1] - m[1]) > 0.001) requestAnimationFrame(tick);
    else running = false;
  };
  const go = () => { if (!running && !document.hidden) { running = true; last = performance.now(); requestAnimationFrame(tick); } };

  size(); render();
  addEventListener("resize", () => { size(); render(); });
  addEventListener(THEME_EVENT, render);
  if (animate) addEventListener("pointermove", e => {
    target[0] = e.clientX / innerWidth; target[1] = 1 - e.clientY / innerHeight;
    amp = Math.min(1, amp + Math.hypot(e.movementX || 0, e.movementY || 0) / 120);
    go();
  }, { passive: true });
  return true;
}
