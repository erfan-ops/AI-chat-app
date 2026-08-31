/**
 * Date helpers. The backend serializes Oracle TIMESTAMP values as naive UTC
 * ISO strings (e.g. "2026-08-21T08:15:09") — without a zone suffix, browsers
 * would parse them as local time, so we append "Z" before parsing.
 */

export function parseIsoUtc(value: string): Date {
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? new Date(0) : date
}

const timeFormatter = new Intl.DateTimeFormat(undefined, {
  hour: '2-digit',
  minute: '2-digit',
})

/** "14:05" — used inside message bubbles. */
export function formatTime(value: string): string {
  return timeFormatter.format(parseIsoUtc(value))
}

const dateFormatter = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
})

const dateWithYearFormatter = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
})

const weekdayFormatter = new Intl.DateTimeFormat(undefined, { weekday: 'long' })

function startOfDay(date: Date): number {
  const d = new Date(date)
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

/** Label for a day divider in the message list: "Today", "Yesterday",
 *  "Monday", or "Aug 18" (with the year once it differs). */
export function formatDayLabel(value: string): string {
  const date = parseIsoUtc(value)
  const today = startOfDay(new Date())
  const day = startOfDay(date)
  const diffDays = Math.round((today - day) / 86_400_000)

  if (diffDays === 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'
  if (diffDays > 1 && diffDays < 7) return weekdayFormatter.format(date)
  return date.getFullYear() === new Date().getFullYear()
    ? dateFormatter.format(date)
    : dateWithYearFormatter.format(date)
}

export function isSameDay(a: string, b: string): boolean {
  return startOfDay(parseIsoUtc(a)) === startOfDay(parseIsoUtc(b))
}

const relativeMinuteFormatter = new Intl.RelativeTimeFormat(undefined, {
  numeric: 'auto',
  style: 'narrow',
})

/** Compact relative timestamp for the conversation list: "now", "5m", "3h",
 *  "Yesterday", "Tuesday", or "Aug 18" (with year once it differs). */
export function formatRelativeTime(value: string): string {
  const date = parseIsoUtc(value)
  const diffMs = date.getTime() - Date.now()
  const diffMinutes = Math.round(diffMs / 60_000)

  if (diffMinutes > -1) return 'now'
  if (diffMinutes > -60) return relativeMinuteFormatter.format(diffMinutes, 'minute')

  const diffHours = Math.round(diffMs / 3_600_000)
  if (diffHours > -24 && startOfDay(date) === startOfDay(new Date())) {
    return relativeMinuteFormatter.format(diffHours, 'hour')
  }
  return formatDayLabel(value)
}
