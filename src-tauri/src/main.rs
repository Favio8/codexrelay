#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use serde_json::{json, Value};
use std::{io::Write, path::PathBuf, process::{Command, Stdio}};
use tauri::{Manager, Emitter, menu::{Menu,MenuItem}, tray::TrayIconBuilder};
#[cfg(windows)] use std::os::windows::process::CommandExt;

fn request(app: &tauri::AppHandle, method: &str, params: Value) -> Result<Value,String> {
 let runtime = if cfg!(debug_assertions) { PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../runtime") } else { app.path().resource_dir().map_err(|e|e.to_string())?.join("runtime") };
 let python = runtime.join("python.exe");
 if !python.is_file() { return Err("运行时尚未准备，请先运行 scripts/prepare-runtime.ps1。".into()); }
 let mut command=Command::new(python);
 command.args(["-I","-X","utf8","-m","codexrelay","rpc"]).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
 command.env_remove("PYTHONPATH").env_remove("PYTHONHOME").env_remove("DEEPSEEK_API_KEY").env_remove("OPENAI_API_KEY");
 #[cfg(windows)] command.creation_flags(0x08000000);
 let mut child=command.spawn().map_err(|e|format!("无法启动运行时：{e}"))?;
 let body=serde_json::to_vec(&json!({"method":method,"params":params})).map_err(|e|e.to_string())?;
 child.stdin.take().ok_or("无法连接运行时")?.write_all(&body).map_err(|e|e.to_string())?;
 let output=child.wait_with_output().map_err(|e|e.to_string())?;
 if !output.status.success() { return Err(format!("运行时退出 ({})，请运行诊断。",output.status)); }
 serde_json::from_slice(&output.stdout).map_err(|_|"运行时返回了无法解析的数据".into())
}
#[tauri::command]
async fn relay_request(app:tauri::AppHandle,method:String,params:Value)->Result<Value,String>{
 tauri::async_runtime::spawn_blocking(move||request(&app,&method,params)).await.map_err(|e|e.to_string())?
}
fn show(app:&tauri::AppHandle){if let Some(w)=app.get_webview_window("main"){let _=w.show();let _=w.set_focus();}}
fn main(){
 tauri::Builder::default()
 .plugin(tauri_plugin_single_instance::init(|app,_,_|show(app)))
 .plugin(tauri_plugin_dialog::init()).invoke_handler(tauri::generate_handler![relay_request])
 .setup(|app|{
  let open=MenuItem::with_id(app,"open","打开 CodexRelay",true,None::<&str>)?;
  let status=MenuItem::with_id(app,"status","正在读取委托状态…",false,None::<&str>)?;
  let enable=MenuItem::with_id(app,"enable","开启新委托",true,None::<&str>)?;
  let disable=MenuItem::with_id(app,"disable","关闭新委托",true,None::<&str>)?;
  let quit=MenuItem::with_id(app,"quit","退出管理窗口（保留委托设置）",true,None::<&str>)?;
  let menu=Menu::with_items(app,&[&status,&open,&enable,&disable,&quit])?;
  let mut tray=TrayIconBuilder::new().menu(&menu).tooltip("CodexRelay · 子代理控制中心").on_menu_event(|app,event|{
   match event.id.as_ref(){
    "open"=>show(app), "quit"=>app.exit(0),
    "enable"|"disable"=>{let handle=app.clone(); let enabled=event.id.as_ref()=="enable"; std::thread::spawn(move||{let result=request(&handle,"set_enabled",json!({"enabled":enabled}));let _=handle.emit("relay-changed",());let error=match result {Err(e)=>Some(e),Ok(v) if v["ok"]!=true=>Some(v["error"].as_str().unwrap_or("操作未完成").to_string()),_=>None};if let Some(e)=error{let _=handle.emit("relay-error",e);show(&handle);}});}, _=>{}
   }
  });
  if let Some(icon)=app.default_window_icon(){tray=tray.icon(icon.clone());}
  let tray=tray.build(app)?;
  let handle=app.handle().clone();
  std::thread::spawn(move||loop{
   if let Ok(value)=request(&handle,"snapshot",json!({})){
    if value["ok"]==true{
     let enabled=value["data"]["enabled"].as_bool().unwrap_or(false);
     let count=value["data"]["tasks"].as_array().map(|tasks|tasks.iter().filter(|t|matches!(t["status"].as_str(),Some("queued"|"running"|"cancelling"))).count()).unwrap_or(0);
     let text=format!("{} · {} 个活动任务",if enabled{"已开启"}else{"已关闭"},count);
     let _=status.set_text(&text); let _=tray.set_tooltip(Some(format!("CodexRelay · {text}")));
    }
   }
   std::thread::sleep(std::time::Duration::from_secs(2));
  });
  Ok(())
 }).on_window_event(|window,event|{if let tauri::WindowEvent::CloseRequested{api,..}=event{api.prevent_close();let _=window.hide();}})
 .run(tauri::generate_context!()).expect("Unable to launch CodexRelay");
}
