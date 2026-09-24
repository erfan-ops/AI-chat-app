import { apiRequest } from './client'
import type {
  LoginOtpMethodRequest,
  LoginRequest,
  LoginResponse,
  OtpChallenge,
  OtpEnableRequest,
  OtpRequiredResponse,
  OtpVerifyRequest,
  PasswordChangeRequest,
  RegisterRequest,
  User,
  UserUpdate,
} from '../types/api'

export function register(request: RegisterRequest): Promise<User> {
  return apiRequest<User>('/auth/register', {
    method: 'POST',
    body: request,
    authenticated: false,
  })
}

/** First step: either a token, or — when two-step verification is on — a challenge
 *  that must be completed with {@link loginWithOtp}. */
export function login(request: LoginRequest): Promise<LoginResponse | OtpRequiredResponse> {
  return apiRequest<LoginResponse | OtpRequiredResponse>('/auth/login', {
    method: 'POST',
    body: request,
    authenticated: false,
  })
}

/** Second step of a two-step login. Unauthenticated on purpose: there is no token
 *  yet, and a 401 here would sign the caller out of an unrelated session. */
export function loginWithOtp(request: OtpVerifyRequest): Promise<LoginResponse> {
  return apiRequest<LoginResponse>('/auth/login/otp', {
    method: 'POST',
    body: request,
    authenticated: false,
  })
}

/** Send this login's code through the other delivery method — a choice for this
 *  login only, which never changes the account's saved default. */
export function switchOtpMethod(request: LoginOtpMethodRequest): Promise<OtpRequiredResponse> {
  return apiRequest<OtpRequiredResponse>('/auth/login/otp/method', {
    method: 'POST',
    body: request,
    authenticated: false,
  })
}

export function getMe(): Promise<User> {
  return apiRequest<User>('/me')
}

export function updateMe(request: UserUpdate): Promise<User> {
  return apiRequest<User>('/me', { method: 'PATCH', body: request })
}

/** Replace the password. A wrong current password is a 400, never a 401 — this
 *  client signs out on any authenticated 401. */
export function changePassword(request: PasswordChangeRequest): Promise<User> {
  return apiRequest<User>('/me/password', { method: 'POST', body: request })
}

/** Send a verification code to the given contact — a local 10-digit mobile number
 *  or an email address, per `method`. Nothing is stored until it is confirmed with
 *  {@link verifyOtpEnable}. */
export function startOtpEnable(request: OtpEnableRequest): Promise<OtpChallenge> {
  return apiRequest<OtpChallenge>('/me/otp/enable', { method: 'POST', body: request })
}

/** Confirm the code: stores the verified contact and turns two-step on. */
export function verifyOtpEnable(request: OtpVerifyRequest): Promise<User> {
  return apiRequest<User>('/me/otp/verify', { method: 'POST', body: request })
}

/** Turn two-step off. The verified contacts are kept. */
export function disableOtp(): Promise<User> {
  return apiRequest<User>('/me/otp/disable', { method: 'POST' })
}
