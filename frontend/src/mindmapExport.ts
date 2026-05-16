import type { Markmap } from 'markmap-view'
import JSZip from 'jszip'

export type MindmapTreeNode = {
  id?: string
  label?: string
  children?: MindmapTreeNode[]
}

export function sanitizeFilename(base: string, ext: string): string {
  const s = base.replace(/[/\\?%*:|"<>]/g, '_').trim().slice(0, 80) || 'mindmap'
  return `${s}.${ext}`
}

export function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

function escapeXml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}

function topicId(raw: string | undefined, fallback: string): string {
  const s = String(raw ?? '')
    .trim()
    .replace(/[^a-zA-Z0-9_-]/g, '_')
    .slice(0, 128)
  return s || fallback
}

function topicXml(node: MindmapTreeNode, isRoot: boolean, seq: { n: number }): string {
  const id = topicId(node.id, `topic-auto-${seq.n++}`)
  const title = escapeXml(String(node.label ?? '').trim() || '（空）')
  const kids = Array.isArray(node.children) ? node.children.filter(Boolean) : []
  const rootAttr = isRoot ? ' structure-class="org.xmind.ui.logic.right"' : ''
  let body = `<title>${title}</title>`
  if (kids.length) {
    body += `<children><topics type="attached">${kids.map((c) => topicXml(c, false, seq)).join('')}</topics></children>`
  }
  return `<topic id="${escapeXml(id)}"${rootAttr}>${body}</topic>`
}

/** XMind 8+ 可打开的 .xmind（ZIP：META-INF/manifest.xml + content.xml） */
export async function buildXmindArchiveBlob(root: MindmapTreeNode): Promise<Blob> {
  const seq = { n: 0 }
  const inner = topicXml(root, true, seq)
  const content = `<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<xmap-content xmlns="urn:xmind:xmap:xmlns:content:2.0" xmlns:fo="http://www.w3.org/1999/XSL/Format" version="2.0">
  <sheet id="sheet-1">${inner}</sheet>
</xmap-content>`
  const manifest = `<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<manifest xmlns="urn:xmind:xmap:xmlns:manifest:1.0">
  <file-entry full-path="content.xml" media-type="text/xml"/>
  <file-entry full-path="META-INF/manifest.xml" media-type="text/xml"/>
</manifest>`
  const zip = new JSZip()
  zip.file('META-INF/manifest.xml', manifest)
  zip.file('content.xml', content)
  return zip.generateAsync({ type: 'blob', compression: 'DEFLATE' })
}

async function nextFrame(): Promise<void> {
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
}

type SizeSnapshot = { width: string; height: string }

function readSizeSnapshot(el: SVGSVGElement): SizeSnapshot {
  return {
    width: el.style.width,
    height: el.style.height,
  }
}

function applySize(el: SVGSVGElement, w: number, h: number): void {
  el.style.width = `${Math.max(320, Math.ceil(w))}px`
  el.style.height = `${Math.max(240, Math.ceil(h))}px`
}

function restoreSize(el: SVGSVGElement, snap: SizeSnapshot): void {
  el.style.width = snap.width
  el.style.height = snap.height
}

/**
 * 临时放大 SVG 视口并 fit，便于导出完整导图；调用方需在 finally 中配合 restoreMarkmapView。
 */
export async function prepareMarkmapForExport(mm: Markmap, svgEl: SVGSVGElement, padding = 56): Promise<SizeSnapshot> {
  const snap = readSizeSnapshot(svgEl)
  const { x1, y1, x2, y2 } = mm.state.rect
  const cw = x2 - x1 + padding * 2
  const ch = y2 - y1 + padding * 2
  applySize(svgEl, cw, ch)
  await nextFrame()
  await mm.fit(Math.max(mm.options.maxInitialScale, 12))
  await nextFrame()
  return snap
}

export async function restoreMarkmapView(mm: Markmap, svgEl: SVGSVGElement, snap: SizeSnapshot): Promise<void> {
  restoreSize(svgEl, snap)
  await nextFrame()
  await mm.fit(mm.options.maxInitialScale)
}

function injectSvgExportStyles(clone: SVGSVGElement, css: string): void {
  const ns = 'http://www.w3.org/2000/svg'
  let defs = clone.querySelector('defs')
  if (!defs) {
    defs = document.createElementNS(ns, 'defs')
    clone.insertBefore(defs, clone.firstChild)
  }
  const styleEl = document.createElementNS(ns, 'style')
  styleEl.setAttribute('type', 'text/css')
  styleEl.textContent = css
  defs.insertBefore(styleEl, defs.firstChild)
}

async function buildExportSvgString(mm: Markmap, svgEl: SVGSVGElement): Promise<string> {
  const clone = svgEl.cloneNode(true) as SVGSVGElement
  const r = svgEl.getBoundingClientRect()
  const pw = Math.ceil(r.width)
  const ph = Math.ceil(r.height)
  if (pw > 0 && ph > 0) {
    clone.setAttribute('width', String(pw))
    clone.setAttribute('height', String(ph))
  }
  const vb = svgEl.getAttribute('viewBox')
  if (vb) clone.setAttribute('viewBox', vb)
  const css = mm.getStyleContent()
  injectSvgExportStyles(clone, css)
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink')
  const serializer = new XMLSerializer()
  return `<?xml version="1.0" encoding="UTF-8"?>\n${serializer.serializeToString(clone)}`
}

/** 导出当前渲染结果的矢量图（含 markmap 主题样式） */
export async function exportMarkmapSvgBlob(mm: Markmap, svgEl: SVGSVGElement): Promise<Blob> {
  const snap = await prepareMarkmapForExport(mm, svgEl)
  try {
    const raw = await buildExportSvgString(mm, svgEl)
    return new Blob([raw], { type: 'image/svg+xml;charset=utf-8' })
  } finally {
    await restoreMarkmapView(mm, svgEl, snap)
  }
}

/** 将 SVG 栅格化为 PNG（依赖浏览器对 SVG 的绘制支持） */
export async function exportMarkmapPngBlob(mm: Markmap, svgEl: SVGSVGElement, scale = 2): Promise<Blob> {
  const snap = await prepareMarkmapForExport(mm, svgEl)
  try {
    const raw = await buildExportSvgString(mm, svgEl)
    const svgBlob = new Blob([raw], { type: 'image/svg+xml;charset=utf-8' })
    const url = URL.createObjectURL(svgBlob)
    try {
      const img = new Image()
      img.decoding = 'async'
      const loaded = new Promise<void>((resolve, reject) => {
        img.onload = () => resolve()
        img.onerror = () => reject(new Error('思维导图 PNG：无法解码 SVG'))
      })
      img.src = url
      await loaded

      const w = img.naturalWidth || img.width
      const h = img.naturalHeight || img.height
      if (!w || !h) throw new Error('思维导图 PNG：尺寸无效')

      const canvas = document.createElement('canvas')
      canvas.width = Math.ceil(w * scale)
      canvas.height = Math.ceil(h * scale)
      const ctx = canvas.getContext('2d')
      if (!ctx) throw new Error('思维导图 PNG：Canvas 不可用')

      ctx.scale(scale, scale)
      ctx.fillStyle = '#0b1220'
      ctx.fillRect(0, 0, w, h)
      ctx.drawImage(img, 0, 0)

      return await new Promise<Blob>((resolve, reject) => {
        canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('思维导图 PNG：编码失败'))), 'image/png')
      })
    } finally {
      URL.revokeObjectURL(url)
    }
  } finally {
    await restoreMarkmapView(mm, svgEl, snap)
  }
}
