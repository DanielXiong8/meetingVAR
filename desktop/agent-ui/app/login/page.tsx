"use client"
import { useRouter } from "next/navigation"
import { LoginForm } from "@/sources/login/components/login-form"

export default function LoginPage() {
  const router = useRouter()
  return <div className="flex min-h-svh flex-col items-center justify-center bg-muted p-6 md:p-10" onSubmit={(event) => { event.preventDefault(); router.push('/workspace') }}><div className="w-full max-w-sm md:max-w-3xl"><LoginForm /></div></div>
}
