import { apiRequest } from './client'
import type {
  GoogleSignInRequest,
  LoginOtpMethodRequest,
  LoginRequest,
  LoginResponse,
  OtpChallenge,
  OtpEnableRequest,
  OtpRequiredResponse,
  OtpVerifyRequest,
  PasswordChangeRequest,
  PasswordResetAuthorization,
  PasswordResetCompleteRequest,
  PasswordResetOptions,
  PasswordResetRequest,
  RegisterRequest,
  TotpEnrollment,
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

/** Sign in with Google: exchanges the credential Google Identity Services handed the
 *  browser for the same access token {@link login} returns — or an OTP challenge, when
 *  the account has two-step verification on. Unauthenticated: there is no token yet.
 *
 *  The credential is opaque here. Only the backend can verify it, and it does so
 *  against Google's keys before believing anything in it. */
export function signInWithGoogle(request: GoogleSignInRequest): Promise<LoginResponse | OtpRequiredResponse> {
  return apiRequest<LoginResponse | OtpRequiredResponse>('/auth/google', {
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

/** Start a password recovery: which methods the account can be recovered by, and —
 *  when `method` is given — a code sent through it.
 *
 *  Unauthenticated by definition (that is why the user is here). The answer says the
 *  same thing whether or not the account exists, so nothing here can be read as
 *  "that account is real". */
export function requestPasswordReset(
  request: PasswordResetRequest,
): Promise<PasswordResetOptions> {
  return apiRequest<PasswordResetOptions>('/auth/password-reset/request', {
    method: 'POST',
    body: request,
    authenticated: false,
  })
}

/** Exchange the code for the authorization that allows one password change.
 *
 *  The code is checked server-side; this call proves nothing on its own and changes
 *  nothing. What it returns is what {@link completePasswordReset} requires. */
export function verifyPasswordResetCode(
  request: OtpVerifyRequest,
): Promise<PasswordResetAuthorization> {
  return apiRequest<PasswordResetAuthorization>('/auth/password-reset/verify', {
    method: 'POST',
    body: request,
    authenticated: false,
  })
}

/** Set the new password. The authorization is the only accepted proof: it is bound
 *  to one account, expires ten minutes after the code was verified, and is spent by
 *  the first successful call. */
export function completePasswordReset(
  request: PasswordResetCompleteRequest,
): Promise<User> {
  return apiRequest<User>('/auth/password-reset/complete', {
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

/** Provision an authenticator app: generates and stores a secret and returns the
 *  otpauth URI (for a QR code) plus the secret for entering by hand.
 *
 *  Nothing is enabled by this call — the code the app produces has to come back
 *  through {@link verifyOtpEnable} first. Enrolling again replaces the secret. */
export function startTotpEnable(): Promise<TotpEnrollment> {
  return apiRequest<TotpEnrollment>('/me/totp/enable', { method: 'POST' })
}

/** Confirm the code: stores the verified contact and turns two-step on. */
export function verifyOtpEnable(request: OtpVerifyRequest): Promise<User> {
  return apiRequest<User>('/me/otp/verify', { method: 'POST', body: request })
}

/** Turn two-step off. The verified contacts are kept. */
export function disableOtp(): Promise<User> {
  return apiRequest<User>('/me/otp/disable', { method: 'POST' })
}
