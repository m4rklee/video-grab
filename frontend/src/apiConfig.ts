/** localStorage key for optional absolute backend origin (e.g. http://127.0.0.1:8028). */
export const API_BASE_STORAGE_KEY = 'shiying_api_base_url'

/** Optional Bilibili login cookie for search / probe (sent to your backend only). */
export const BILIBILI_COOKIE_STORAGE_KEY = 'shiying_bilibili_cookie'

export const OPENAI_API_KEY_STORAGE_KEY = 'shiying_openai_api_key'
export const OPENAI_BASE_URL_STORAGE_KEY = 'shiying_openai_base_url'
export const SUMMARIZE_MODEL_STORAGE_KEY = 'shiying_summarize_model'

export function loadStoredApiBase(): string {
  try {
    return localStorage.getItem(API_BASE_STORAGE_KEY)?.trim() ?? ''
  } catch {
    return ''
  }
}

export function persistApiBase(url: string): void {
  const t = url.trim().replace(/\/$/, '')
  try {
    if (t) localStorage.setItem(API_BASE_STORAGE_KEY, t)
    else localStorage.removeItem(API_BASE_STORAGE_KEY)
  } catch {
    // ignore quota / private mode
  }
}

export function loadStoredBilibiliCookie(): string {
  try {
    return localStorage.getItem(BILIBILI_COOKIE_STORAGE_KEY)?.trim() ?? ''
  } catch {
    return ''
  }
}

export function persistBilibiliCookie(value: string): void {
  const t = value.trim()
  try {
    if (t) localStorage.setItem(BILIBILI_COOKIE_STORAGE_KEY, t)
    else localStorage.removeItem(BILIBILI_COOKIE_STORAGE_KEY)
  } catch {
    // ignore quota / private mode
  }
}

export function loadStoredOpenAiApiKey(): string {
  try {
    return localStorage.getItem(OPENAI_API_KEY_STORAGE_KEY)?.trim() ?? ''
  } catch {
    return ''
  }
}

export function loadStoredOpenAiBaseUrl(): string {
  try {
    return localStorage.getItem(OPENAI_BASE_URL_STORAGE_KEY)?.trim() ?? ''
  } catch {
    return ''
  }
}

export function loadStoredSummarizeModel(): string {
  try {
    return localStorage.getItem(SUMMARIZE_MODEL_STORAGE_KEY)?.trim() ?? ''
  } catch {
    return ''
  }
}

export function persistOpenAiApiKey(value: string): void {
  const t = value.trim()
  try {
    if (t) localStorage.setItem(OPENAI_API_KEY_STORAGE_KEY, t)
    else localStorage.removeItem(OPENAI_API_KEY_STORAGE_KEY)
  } catch {
    // ignore
  }
}

export function persistOpenAiBaseUrl(value: string): void {
  const t = value.trim().replace(/\/$/, '')
  try {
    if (t) localStorage.setItem(OPENAI_BASE_URL_STORAGE_KEY, t)
    else localStorage.removeItem(OPENAI_BASE_URL_STORAGE_KEY)
  } catch {
    // ignore
  }
}

export function persistSummarizeModel(value: string): void {
  const t = value.trim()
  try {
    if (t) localStorage.setItem(SUMMARIZE_MODEL_STORAGE_KEY, t)
    else localStorage.removeItem(SUMMARIZE_MODEL_STORAGE_KEY)
  } catch {
    // ignore
  }
}

/** Per-request header; backend prefers this over server env for Bilibili API. */
export function bilibiliCookieRequestHeaders(): Record<string, string> {
  const cookie = loadStoredBilibiliCookie()
  if (!cookie) return {}
  return { 'X-Bilibili-Cookie': cookie }
}

/** Per-request LLM settings; backend prefers headers over server env. */
export function llmRequestHeaders(): Record<string, string> {
  const headers: Record<string, string> = {}
  const key = loadStoredOpenAiApiKey()
  if (key) headers['X-OpenAI-Api-Key'] = key
  const base = loadStoredOpenAiBaseUrl()
  if (base) headers['X-OpenAI-Base-Url'] = base
  const model = loadStoredSummarizeModel()
  if (model) headers['X-Summarize-Model'] = model
  return headers
}

export function userSettingsRequestHeaders(): Record<string, string> {
  return { ...bilibiliCookieRequestHeaders(), ...llmRequestHeaders() }
}

/** When `base` is empty, returns `path` (relative → same origin / Vite proxy). */
export function withApiBase(base: string, path: string): string {
  const b = base.trim().replace(/\/$/, '')
  const p = path.startsWith('/') ? path : `/${path}`
  if (!b) return p
  return `${b}${p}`
}
