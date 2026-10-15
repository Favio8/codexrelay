import { useState } from 'react';
import { Cpu, ShieldCheck, Trash2, Pencil } from 'lucide-react';
import ConnectionTest from './ConnectionTest';
import ProbeEvidence from './ProbeEvidence';
import { type Provider } from './api';

interface ProviderListProps {
  providers: Provider[];
  activeProviderId: string | null;
  busy: boolean;
  onEdit: (provider: Provider) => void;
  onChanged: () => void;
  onActivate: (id: string) => void;
  onDelete: (provider: Provider) => void;
}

export default function ProviderList({ providers, activeProviderId, busy, onEdit, onChanged, onActivate, onDelete }: ProviderListProps) {
  const [testingId,setTestingId]=useState('');
  const [slots,setSlots]=useState<Record<string,string>>({});
  const locked=busy||!!testingId;
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
              <label className="field">测试模型<select aria-label={'测试模型 '+p.name} disabled={locked} value={slots[p.id]||'flash'} onChange={e=>setSlots({...slots,[p.id]:e.target.value})}><option value="flash">普通模型</option><option value="pro">增强模型</option></select></label>
              <ConnectionTest method="test_provider" params={{id:p.id,slot:slots[p.id]||'flash'}} identityKey={p.revision} compact disabled={busy||(!!testingId&&testingId!==p.id)} onChanged={onChanged} onBusyChange={testing=>setTestingId(testing?p.id:'')}>
               <button className="icon-btn" aria-label="编辑" title="编辑" disabled={locked} onClick={() => onEdit(p)}><Pencil size={16}/></button>
               <button className={active?'current-use':'primary'} disabled={locked||active} title={kindLabel} onClick={() => onActivate(p.id)}>{active?'使用中':'使用'}</button>
               <button className="icon-btn danger" aria-label={'删除 ' + p.name} title="删除" disabled={locked} onClick={() => onDelete(p)}><Trash2 size={16} /></button>
              </ConnectionTest>
              <ProbeEvidence provider={p} slot={slots[p.id]||'flash'}/>
            </div>
          </section>
        );
      })}
    </div>
  );
}
