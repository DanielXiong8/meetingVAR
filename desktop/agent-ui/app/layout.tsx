import type { Metadata, Viewport } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: '会议 agent · AI 会议工作台',
  description: '一个把会议、项目与行动项连接起来的 AI 工作台。',
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
            __html: `(function(){function mark(){var w=window;if(w.pywebview&&w.pywebview.api){if(!w.__mvBridgeReady){w.__mvBridgeReady=true;try{w.dispatchEvent(new Event('mvbridge'))}catch(e){}}if(!w.__mvPageReady){w.__mvPageReady=true;try{w.pywebview.api.page_ready({page:'home',version:'0.28.4'})}catch(e){}}}}
mark();window.addEventListener('pywebviewready',mark);document.addEventListener('DOMContentLoaded',mark);})();`,
          }}
        />
      </head>
      <body className="antialiased">
        {children}
      </body>
    </html>
  )
}
