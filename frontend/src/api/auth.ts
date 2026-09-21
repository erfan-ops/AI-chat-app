import { apiRequest } from './client'
import type {
  LoginRequest,
  LoginResponse,
  OtpChallenge,
  OtpEnableRequest,
  OtpRequiredResponse,
  OtpVerifyRequest,
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

export function getMe(): Promise<User> {
  return apiRequest<User>('/me')
}

export function updateMe(request: UserUpdate): Promise<User> {
  return apiRequest<User>('/me', { method: 'PATCH', body: request })
}

/** Send a verification code to the given local 10-digit mobile number.
 *  Nothing is stored until it is confirmed with {@link verifyOtpEnable}. */
export function startOtpEnable(request: OtpEnableRequest): Promise<OtpChallenge> {
  return apiRequest<OtpChallenge>('/me/otp/enable', { method: 'POST', body: request })
}

/** Confirm the code: stores the verified number and turns two-step on. */
export function verifyOtpEnable(request: OtpVerifyRequest): Promise<User> {
  return apiRequest<User>('/me/otp/verify', { method: 'POST', body: request })
}

/** Turn two-step off. The verified number is kept. */
export function disableOtp(): Promise<User> {
  return apiRequest<User>('/me/otp/disable', { method: 'POST' })
}
