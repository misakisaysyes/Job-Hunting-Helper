import { useState } from "react";
import { uploadResume } from "../../api/settings";

export default function ResumeUploadField({ currentPath, onUploaded, onUploadingChange }) {
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState(null);

  async function chooseFile(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".md")) {
      setNotice({ type: "error", text: "请选择 .md 格式的简历文件。" });
      return;
    }
    setUploading(true);
    onUploadingChange(true);
    setNotice(null);
    try {
      const result = await uploadResume(file);
      onUploaded(result.resume_path);
      setNotice({ type: "success", text: `已上传 ${result.filename}，下轮 AI 评分和招呼生成将使用这份简历。` });
    } catch (cause) {
      setNotice({ type: "error", text: cause.message || "上传简历失败" });
    } finally {
      setUploading(false);
      onUploadingChange(false);
    }
  }

  return <div className="config-field config-resume-field">
    <label htmlFor="config-resume-upload">简历（Markdown）</label>
    <input id="config-resume-upload" type="file" accept=".md,text/markdown"
      onChange={chooseFile} disabled={uploading} />
    <p>选择 .md 文件后自动上传并保存，文件须为 UTF-8 编码且不超过 1 MB。</p>
    <div className="config-resume-current">
      <span>当前简历</span>
      <code>{currentPath}</code>
    </div>
    {uploading && <p role="status">正在上传简历…</p>}
    {notice && <p className={`config-resume-notice ${notice.type}`} role="status">{notice.text}</p>}
  </div>;
}
