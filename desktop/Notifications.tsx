import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { Check, CircleHelp, X } from 'lucide-react';

type Kind = 'success' | 'error';
interface Notification { id:number; message:string; kind:Kind }
const Notifications = createContext<{notify:(message:string,kind?:Kind)=>void;dismiss:()=>void}>({notify:()=>{},dismiss:()=>{}});
export const useNotifications = () => useContext(Notifications);

function Toast({notification,onClose}:{notification:Notification;onClose:()=>void}) {
 const remaining=useRef(notification.kind==='error'?6000:2000);
 const [hovered,setHovered]=useState(false),[focused,setFocused]=useState(false),[hidden,setHidden]=useState(document.hidden);
 useEffect(()=>{const change=()=>setHidden(document.hidden);document.addEventListener('visibilitychange',change);return()=>document.removeEventListener('visibilitychange',change);},[]);
 useEffect(()=>{
  if(hovered||focused||hidden)return;
  const started=performance.now(),timer=setTimeout(onClose,remaining.current);
  return()=>{clearTimeout(timer);remaining.current=Math.max(0,remaining.current-(performance.now()-started));};
 },[hovered,focused,hidden,onClose]);
 const Icon=notification.kind==='success'?Check:CircleHelp;
 return <div className={'toast '+notification.kind} role={notification.kind==='error'?'alert':'status'}
  onMouseEnter={()=>setHovered(true)} onMouseLeave={()=>setHovered(false)} onFocus={()=>setFocused(true)}
  onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))setFocused(false);}}>
  <Icon size={18}/><span>{notification.message}</span><button className="icon-btn" aria-label="关闭通知" onClick={onClose}><X size={16}/></button>
 </div>;
}

export default function NotificationProvider({children}:{children:ReactNode}) {
 const [notification,setNotification]=useState<Notification|null>(null),nextId=useRef(0);
 const notify=useCallback((message:string,kind:Kind='success')=>setNotification({id:++nextId.current,message,kind}),[]);
 const close=useCallback(()=>setNotification(null),[]);
 return <Notifications.Provider value={{notify,dismiss:close}}>{children}{notification&&<Toast key={notification.id} notification={notification} onClose={close}/>}</Notifications.Provider>;
}
