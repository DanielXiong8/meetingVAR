import { Background } from "@/sources/newsletter/components/background"
import { Newsletter } from "@/sources/newsletter/components/MeetingNewsletter"

export default function Home() {
  return <main className="p-inset h-[100dvh] w-full newsletter-page"><div className="relative h-full w-full"><Background src="/alt-placeholder.png" /><Newsletter /></div></main>
}
