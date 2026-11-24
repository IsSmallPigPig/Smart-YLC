import type { ReactNode } from 'react'

/** 页面骨架，与演示站一致：标题 + 说明 + 可滚动内容区（复用 fluent.css 的 g-page 体系） */
export function Shell({
  title, caption, actions, children,
}: {
  title: string; caption: string; actions?: ReactNode; children: ReactNode
}) {
  return (
    <div className="f-window">
      <div className="g-page">
        <div className="g-page__head">
          <div>
            <div className="g-page__title">{title}</div>
            <div className="g-page__caption">{caption}</div>
          </div>
          {actions}
        </div>
        <div className="g-page__body f-scroll">{children}</div>
      </div>
    </div>
  )
}

/** 一组卡片：组标题右侧可挂状态文字（setgroup） */
export function Group({ title, aside, children }: { title: string; aside?: string; children: ReactNode }) {
  return (
    <section className="setgroup">
      <h2 className="setgroup__title">
        {title}
        {aside && <span className="setgroup__aside">{aside}</span>}
      </h2>
      <div className="setgroup__body">{children}</div>
    </section>
  )
}

/** 卡片里的一行：左侧文字，右侧控件（setcard） */
export function Row({
  title, content, control, mono = false, error = false,
}: {
  title?: string; content?: string; control?: ReactNode; mono?: boolean; error?: boolean
}) {
  return (
    <div className="setcard">
      <div className="setcard__text">
        {title && <div className={mono ? 'row-title mono' : 'row-title'}>{title}</div>}
        {content && (
          <div className="setcard__content" style={error ? { color: '#c42b1c' } : undefined}>{content}</div>
        )}
      </div>
      {control && <div className="setcard__control row-control">{control}</div>}
    </div>
  )
}
