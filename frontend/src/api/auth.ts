import { apiRequest } from './client'
import type {
  LoginRequest,
  LoginResponse,
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

export function login(request: LoginRequest): Promise<LoginResponse> {
  return apiRequest<LoginResponse>('/auth/login', {
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
