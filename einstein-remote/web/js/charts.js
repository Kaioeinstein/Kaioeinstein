// Gráfico de linhas leve em <canvas> (sem bibliotecas externas).

const COLORS = { purple: "#a855f7", green: "#22e3a0" };

export class LineChart {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {{series: {key:string,color:string}[], max?: number, format?: (v:number)=>string, timeAxis?: boolean}} opts
   */
  constructor(canvas, opts) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.opts = { max: 100, format: (v) => `${Math.round(v)}%`, ...opts };
    this.data = [];
    this.height = Number(canvas.getAttribute("height")) || 200;
    new ResizeObserver(() => this.draw()).observe(canvas);
  }

  set(data) { this.data = data; this.draw(); }

  draw() {
    const { canvas, ctx } = this;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = this.height;
    if (!w) return;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.height = `${h}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const pad = { l: 46, r: 8, t: 10, b: 22 };
    const iw = w - pad.l - pad.r;
    const ih = h - pad.t - pad.b;
    let max = this.opts.max;
    if (max === "auto") {
      max = 1;
      for (const d of this.data) for (const s of this.opts.series) max = Math.max(max, d[s.key] ?? 0);
      max *= 1.15;
    }

    // grade
    ctx.font = "11px Segoe UI, sans-serif";
    ctx.fillStyle = "#6b6b80";
    ctx.strokeStyle = "rgba(255,255,255,0.05)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = pad.t + (ih * i) / 4;
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
      ctx.fillText(this.opts.format(max * (1 - i / 4)), 2, y + 4);
    }
    if (this.data.length < 2) {
      ctx.fillStyle = "#6b6b80";
      ctx.fillText("Aguardando dados…", pad.l + 10, pad.t + ih / 2);
      return;
    }
    const t0 = this.data[0].ts;
    const t1 = this.data[this.data.length - 1].ts;
    const span = Math.max(t1 - t0, 1);
    const xOf = (d) => pad.l + ((d.ts - t0) / span) * iw;

    // eixo do tempo
    ctx.fillStyle = "#6b6b80";
    for (let i = 0; i <= 3; i++) {
      const t = t0 + (span * i) / 3;
      const label = new Date(t * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", ...(span < 600 ? { second: "2-digit" } : {}) });
      const x = pad.l + (iw * i) / 3;
      ctx.textAlign = i === 0 ? "left" : i === 3 ? "right" : "center";
      ctx.fillText(label, x, h - 6);
    }
    ctx.textAlign = "left";

    for (const s of this.opts.series) {
      const color = COLORS[s.color] || s.color;
      const pts = this.data.filter((d) => d[s.key] != null).map((d) => [xOf(d), pad.t + ih * (1 - Math.min(d[s.key], max) / max)]);
      if (pts.length < 2) continue;
      const path = new Path2D();
      path.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) {
        const [x0, y0] = pts[i - 1];
        const [x1, y1] = pts[i];
        const mx = (x0 + x1) / 2;
        path.bezierCurveTo(mx, y0, mx, y1, x1, y1);
      }
      const fill = new Path2D(path);
      fill.lineTo(pts[pts.length - 1][0], pad.t + ih);
      fill.lineTo(pts[0][0], pad.t + ih);
      fill.closePath();
      const grad = ctx.createLinearGradient(0, pad.t, 0, pad.t + ih);
      grad.addColorStop(0, color + "55");
      grad.addColorStop(1, color + "00");
      ctx.fillStyle = grad;
      ctx.fill(fill);
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.shadowColor = color;
      ctx.shadowBlur = 10;
      ctx.stroke(path);
      ctx.shadowBlur = 0;
      const [lx, ly] = pts[pts.length - 1];
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(lx, ly, 3.5, 0, Math.PI * 2); ctx.fill();
    }
  }
}
