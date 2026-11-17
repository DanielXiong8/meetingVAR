'use client'

import { useState } from 'react'
import {
  Archive,
  Bell,
  Bot,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Code2,
  FileText,
  Folder,
  FolderOpen,
  Hash,
  LayoutDashboard,
  MessageSquare,
  Mic,
  MoreHorizontal,
  Paperclip,
  Play,
  Plus,
  Search,
  Send,
  Settings,
  Sparkles,
  SquarePen,
  Users,
  Video,
  X,
  Zap,
} from 'lucide-react'

const navItems = [
  { icon: LayoutDashboard, label: '工作台' },
  { icon: MessageSquare, label: '会议对话', count: '3' },
  { icon: FileText, label: '会议纪要' },
  { icon: Archive, label: '会议档案' },
]

const files = [
  { name: 'meeting-agent', type: 'folder', open: true },
  { name: 'src', type: 'folder', open: true, indent: 1 },
  { name: 'app.tsx', type: 'file', indent: 2, active: true },
  { name: 'meeting.ts', type: 'file', indent: 2 },
  { name: 'prompts.ts', type: 'file', indent: 2 },
  { name: 'components', type: 'folder', open: false, indent: 1 },
  { name: 'README.md', type: 'file', indent: 1 },
  { name: 'package.json', type: 'file', indent: 1 },
]

function IconButton({ label, children }: { label: string; children: React.ReactNode }) {
  return <button aria-label={label} className="icon-button">{children}</button>
}

export default function Home() {
  const [activeNav, setActiveNav] = useState('工作台')
  const [message, setMessage] = useState('')
  const [messages, setMessages] = useState([{ role: 'assistant', text: '你好，我是你的会议 agent。\n我可以帮你准备议程、记录重点、提炼决策，并在会后自动生成行动项。' }])
  const [rightTab, setRightTab] = useState<'files' | 'notes'>('files')
  const [showSettings, setShowSettings] = useState(false)

  function sendMessage() {
    if (!message.trim()) return
    setMessages([...messages, { role: 'user', text: message }, { role: 'assistant', text: '收到。我会结合当前会议上下文帮你整理这件事，并在会议结束后同步到会议纪要。' }])
    setMessage('')
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand-row"><div className="brand-mark"><Sparkles size={15} /></div><span>会议 agent</span><button className="workspace-switcher">个人空间 <ChevronDown size={13} /></button></div>
        <div className="profile-card"><div className="avatar">D</div><div><div className="profile-name">Daniel Xiong</div><div className="profile-sub">Free workspace</div></div><MoreHorizontal size={16} className="muted-icon" /></div>
        <button className="new-meeting"><Plus size={16} /> 新建会议 <span>⌘ N</span></button>
        <nav className="nav-list" aria-label="主导航">
          {navItems.map((item) => <button key={item.label} onClick={() => setActiveNav(item.label)} className={`nav-item ${activeNav === item.label ? 'active' : ''}`}><item.icon size={16} /><span>{item.label}</span>{item.count && <b>{item.count}</b>}</button>)}
        </nav>
        <div className="section-label">最近项目 <button aria-label="添加项目"><Plus size={14} /></button></div>
        <div className="project-list"><button className="project active-project"><span className="project-dot purple" />产品周会 <span className="project-time">刚刚</span></button><button className="project"><span className="project-dot blue" />Q4 复盘 <span className="project-time">昨天</span></button><button className="project"><span className="project-dot green" />招聘面试 <span className="project-time">周一</span></button></div>
        <div className="sidebar-bottom"><button className="nav-item"><Users size={16} /> 团队成员</button><button className="nav-item" onClick={() => setShowSettings(true)}><Settings size={16} /> 设置 <span className="shortcut">⌘ ,</span></button><button className="nav-item"><CircleHelp size={16} /> 帮助中心</button></div>
      </aside>

      <section className="chat-panel">
        <header className="topbar"><div className="breadcrumb"><span className="live-dot" />产品周会 <ChevronRight size={14} /><span className="muted-text">会议对话</span></div><div className="top-actions"><IconButton label="搜索"><Search size={17} /></IconButton><IconButton label="通知"><Bell size={17} /></IconButton><div className="top-avatar">D</div></div></header>
        <div className="chat-content">
          <div className="meeting-header"><div><div className="eyebrow">MONDAY · 10:00 AM</div><h1>产品周会</h1><p>和 agent 一起，让每次会议都有结果。</p></div><button className="record-button"><span className="record-dot" /> 开始录音</button></div>
          <div className="context-chips"><span><Video size={14} /> Google Meet</span><span><Users size={14} /> 4 位参与者</span><span><Zap size={14} /> 自动纪要已开启</span></div>
          <div className="conversation" aria-live="polite">
            {messages.map((item, i) => <div key={i} className={`message-row ${item.role}`}><div className={`message-avatar ${item.role}`}>{item.role === 'assistant' ? <Sparkles size={15} /> : 'D'}</div><div className="message-body"><div className="message-name">{item.role === 'assistant' ? '会议 agent' : '你'} <span>{item.role === 'assistant' ? 'AI 助手' : '刚刚'}</span></div><div className="message-text">{item.text}</div>{i === 0 && <div className="suggestions"><button onClick={() => setMessage('帮我准备今天的会议议程')}>准备会议议程 <ChevronRight size={14} /></button><button onClick={() => setMessage('总结上次会议的行动项')}>总结上次行动项 <ChevronRight size={14} /></button><button onClick={() => setMessage('分析当前项目进度')}>分析项目进度 <ChevronRight size={14} /></button></div>}</div></div>)}
          </div>
          <div className="composer-wrap"><div className="composer"><textarea value={message} onChange={(e) => setMessage(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) { e.preventDefault(); sendMessage() } }} placeholder="问会议 agent 任何问题..." rows={2} /><div className="composer-actions"><div><IconButton label="添加附件"><Paperclip size={16} /></IconButton><button className="model-select"><Bot size={14} /> Agent <ChevronDown size={13} /></button></div><button className="send-button" onClick={sendMessage} aria-label="发送"><Send size={16} /></button></div></div><div className="composer-hint">按 Enter 发送 · Shift + Enter 换行</div></div>
        </div>
      </section>

      <aside className="inspector"><div className="inspector-header"><div className="inspector-tabs"><button className={rightTab === 'files' ? 'selected' : ''} onClick={() => setRightTab('files')}><Code2 size={15} /> 文件</button><button className={rightTab === 'notes' ? 'selected' : ''} onClick={() => setRightTab('notes')}><FileText size={15} /> 纪要</button></div><IconButton label="关闭面板"><X size={16} /></IconButton></div>{rightTab === 'files' ? <><div className="tree-header"><span>EXPLORER</span><div><IconButton label="新建文件"><SquarePen size={14} /></IconButton><IconButton label="更多"><MoreHorizontal size={15} /></IconButton></div></div><div className="file-tree">{files.map((file) => <button key={file.name} className={`tree-item ${file.active ? 'tree-active' : ''}`} style={{ paddingLeft: `${14 + (file.indent || 0) * 16}px` }}>{file.type === 'folder' ? (file.open ? <FolderOpen size={15} className="folder-icon" /> : <Folder size={15} className="folder-icon" />) : <Hash size={14} className="file-icon" />}<span>{file.name}</span>{file.active && <span className="unsaved-dot" />}</button>)}</div><div className="file-preview"><div className="preview-label">当前文件</div><div className="code-window"><div className="code-line"><span className="line-num">1</span><span className="purple-code">import</span> {'{'} <span className="blue-code">meetingAgent</span> {'}'} <span className="purple-code">from</span> <span className="green-code">&quot;@meeting/core&quot;</span></div><div className="code-line"><span className="line-num">2</span></div><div className="code-line"><span className="line-num">3</span><span className="purple-code">const</span> <span className="blue-code">meeting</span> = <span className="purple-code">await</span> meetingAgent.<span className="yellow-code">start</span>({'{'}</div><div className="code-line"><span className="line-num">4</span>  title: <span className="green-code">&quot;产品周会&quot;</span>,</div><div className="code-line"><span className="line-num">5</span>  autoNotes: <span className="orange-code">true</span>,</div><div className="code-line"><span className="line-num">6</span>{'}'})</div></div></div></> : <div className="notes-view"><div className="notes-title">产品周会 · 纪要</div><div className="note-card"><span className="note-icon"><Sparkles size={14} /></span><div><b>待生成会议纪要</b><p>开始录音后，agent 会自动整理重点、决策和行动项。</p></div></div><div className="note-section"><span>会议模板</span><button>产品评审 <ChevronDown size={13} /></button></div><div className="note-section"><span>输出格式</span><button>详细版 <ChevronDown size={13} /></button></div></div>}</aside>

      {showSettings && <div className="modal-backdrop" onClick={() => setShowSettings(false)}><div className="settings-modal" onClick={(e) => e.stopPropagation()}><div className="modal-title"><div><div className="eyebrow">WORKSPACE</div><h2>设置</h2></div><IconButton label="关闭设置"><X size={18} /></IconButton></div><div className="settings-layout"><div className="settings-menu"><button className="settings-selected">常规</button><button>我的账户</button><button>会议偏好</button><button>团队与权限</button><button>集成</button><button>账单</button></div><div className="settings-body"><h3>常规设置</h3><p className="settings-description">管理你的工作区和会议 agent 偏好。</p><label>工作区名称<input defaultValue="个人空间" /></label><label>默认会议模板<select defaultValue="产品评审"><option>产品评审</option><option>一对一</option><option>团队周会</option></select></label><div className="setting-toggle"><div><b>自动生成会议纪要</b><span>会议结束后自动整理重点内容</span></div><div className="toggle on" /></div><div className="setting-toggle"><div><b>桌面通知</b><span>会议开始前提醒我</span></div><div className="toggle on" /></div><button className="save-button" onClick={() => setShowSettings(false)}>保存更改</button></div></div></div></div>}
    </main>
  )
}
