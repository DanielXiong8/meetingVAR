import type { Metadata, Viewport } from 'next'
import '@fontsource/geist/400.css'
import '@fontsource/geist/500.css'
import '@fontsource/geist/600.css'
import '@fontsource/geist/700.css'
import '@fontsource/geist-mono/400.css'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/instrument-serif/400.css'
import '@fontsource/instrument-serif/400-italic.css'
import { GenerationProvider } from '@/sources/folders/contexts/generation-context'
import './globals.css'

export const metadata: Metadata = {
  title: 'meetingVAR · 会议证据层',
  description: '把每句话、每个决定，和它们发生的那一刻连接起来。',
  generator: 'v0.app',
  icons: {
    icon: [
      {
        url: '/icon-light-32x32.png',
        media: '(prefers-color-scheme: light)',
      },
      {
        url: '/icon-dark-32x32.png',
        media: '(prefers-color-scheme: dark)',
      },
      {
        url: '/icon.svg',
        type: 'image/svg+xml',
      },
    ],
    apple: '/apple-icon.png',
  },
}

export const viewport: Viewport = {
  colorScheme: 'light dark',
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: 'white' },
    { media: '(prefers-color-scheme: dark)', color: 'black' },
  ],
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="zh-CN">
      <head>
        {/* Runs before React hydration: acknowledge the pywebview bridge even if
            the window is occluded and React effects/timers are throttled. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){function mark(){var w=window;if(w.pywebview&&w.pywebview.api){if(!w.__mvBridgeReady){w.__mvBridgeReady=true;try{w.dispatchEvent(new Event('mvbridge'))}catch(e){}}if(!w.__mvPageReady){w.__mvPageReady=true;try{w.pywebview.api.page_ready({page:location.pathname.indexOf('/workspace')!==-1?'workspace':'home',version:'0.28.5'})}catch(e){}}}}
mark();window.addEventListener('pywebviewready',mark);document.addEventListener('DOMContentLoaded',mark);})();`,
          }}
        />
      </head>
      <body className="antialiased">
        <GenerationProvider>{children}</GenerationProvider>
      </body>
    </html>
  )
}
