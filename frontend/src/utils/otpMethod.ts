import type { OtpMethod } from '../types/api'

/** Choices, in the order they are offered. */
export const OTP_METHODS: readonly OtpMethod[] = ['SMS', 'EMAIL']

/** How each method reads mid-sentence: "we sent a code by text message". */
const LABELS: Record<OtpMethod, string> = {
  SMS: 'text message',
  EMAIL: 'email',
}

/** How each method reads as a choice: "Text message" / "Email". */
const NAMES: Record<OtpMethod, string> = {
  SMS: 'Text message',
  EMAIL: 'Email',
}

/**
 * Read a delivery method from an API payload or from a stored session.
 *
 * A session is persisted in localStorage and can outlive a deploy, so its user
 * object may predate any field added since. Anything unrecognised therefore means
 * SMS — the same rule the API applies to an unset column — which keeps the client
 * from disagreeing with the server, or crashing on a value it cannot label.
 */
export function parseOtpMethod(value: unknown): OtpMethod {
  return typeof value === 'string' && value.trim().toUpperCase() === 'EMAIL' ? 'EMAIL' : 'SMS'
}

export function otpMethodLabel(value: unknown): string {
  return LABELS[parseOtpMethod(value)]
}

export function otpMethodName(value: unknown): string {
  return NAMES[parseOtpMethod(value)]
}
