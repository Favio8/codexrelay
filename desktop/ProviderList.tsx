import { Cpu, ShieldCheck, Trash2, Pencil, PlugZap } from 'lucide-react';
import { type Provider } from './api';

interface ProviderListProps {
  providers: Provider[];
  activeProviderId: string | null;
  busy: boolean;
  onEdit: (provider: Provider) => void;
  onTest: (id: string) => void;
  onActivate: (id: string) => void;
  onDelete: (provider: Provider) => void;
}

export default function ProviderList({ providers, activeProviderId, busy, onEdit, onTest, onActivate, onDelete }: ProviderListProps) {
  return (
    <div className="provider-list">
      {providers.map(p => {
        const active = p.id === activeProviderId;
        const kindLabel = active ? '当前使用' : p.kind === 'deepseek' ? '官方预设' : '自定义';
        const budget = (p.max_total_tokens || 1000000).toLocaleString();
        return (
          <section key={p.id} className={'provider-row' + (active ? ' active-provider' : '')}>
            <div className="provider-id">
              <span className="provider-logo"><Cpu size={20} /></span>
              <div className="provider-id-text">
                <div className="provider-name">
                  <h2 title={p.name}>{p.name}</h2>
                </div>
                <p className="endpoint" title={p.base_url}>{p.base_url}</p>
              </div>
            </div>
            <div className="provider-meta">
              <span className="meta-item" title={'普通模型 · ' + p.flash_model}>
                <span className="meta-label">普通模型</span>
                <span className="meta-value">{p.flash_model}</span>
              </span>
              <span className="meta-item" title={'增强模型 · ' + p.pro_model}>
                <span className="meta-label">增强模型</span>
                <span className="meta-value">{p.pro_model}</span>
              </span>
            </div>
            <div className="provider-budget" title={'任务预算上限 · ' + budget + ' tokens'}>
              <span className="budget-value">{budget}</span><small>Token 上限</small>
              <span className={'provider-credential '+(p.has_key?'saved':'')} title={p.has_key?'API Key 已保存在 Windows 凭据库':'尚未设置 API Key'}><ShieldCheck size={11}/>{p.has_key?'已保存':'未设置 Key'}</span>
            </div>
            <div className="provider-actions">
              <button className="icon-btn" aria-label="测试连接" title="测试连接" disabled={busy} onClick={() => onTest(p.id)}><PlugZap size={16}/></button>
              <button className="icon-btn" aria-label="编辑" title="编辑" disabled={busy} onClick={() => onEdit(p)}><Pencil size={16}/></button>
              <button className={active?'current-use':'primary'} disabled={busy||active} title={kindLabel} onClick={() => onActivate(p.id)}>{active?'使用中':'使用'}</button>
              <button className="icon-btn danger" aria-label={'删除 ' + p.name} title="删除" disabled={busy} onClick={() => onDelete(p)}><Trash2 size={16} /></button>
            </div>
          </section>
        );
      })}
    </div>
  );
}
