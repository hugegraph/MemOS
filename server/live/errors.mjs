export class ApiError extends Error {
  constructor(message, code = 40000, status = 400) {
    super(message)
    this.code = code
    this.status = status
  }
}
