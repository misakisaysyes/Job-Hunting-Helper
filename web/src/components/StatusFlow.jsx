import React, { useEffect, useRef } from "react";
import { BufferGeometry, DoubleSide, Line, LineBasicMaterial, Mesh, MeshBasicMaterial,
  OrthographicCamera, Scene, Shape, ShapeGeometry, Vector3, WebGLRenderer } from "three";
import { statusInfo } from "../lib/jobStatus";

const HEIGHT = 60;
const NODE_Y = 11;
const NODE_HEIGHT = 34;
const NODE_GAP = 28;
const NODE_WIDTH = 112;

function addArrow(scene, resources, startX, endX, centerY, color) {
  const geometry = new BufferGeometry().setFromPoints([
    new Vector3(startX, centerY, 0),
    new Vector3(endX - 9, centerY, 0),
  ]);
  const material = new LineBasicMaterial({ color });
  scene.add(new Line(geometry, material));
  const tip = new Shape();
  tip.moveTo(0, -5);
  tip.lineTo(9, 0);
  tip.lineTo(0, 5);
  tip.closePath();
  const tipGeometry = new ShapeGeometry(tip);
  const tipMaterial = new MeshBasicMaterial({ color, side: DoubleSide });
  const tipMesh = new Mesh(tipGeometry, tipMaterial);
  tipMesh.position.set(endX - 9, centerY, 0);
  scene.add(tipMesh);
  resources.push(geometry, material, tipGeometry, tipMaterial);
}

export default function StatusFlow({ job, selected, belowThreshold, onSelect }) {
  const hostRef = useRef(null);
  const current = job.job_status === "filtered" || job.job_status === "collected"
    ? "scored" : job.job_status === "monitoring" ? "greeted" : job.job_status;
  const stages = current.startsWith("ended_") ? ["scored", current]
    : current === "greeted" ? ["scored", "greeted"]
      : ["scored", "greeting_ready", "greeted"];
  const activeIndex = stages.indexOf(current);
  const width = 3 + stages.length * NODE_WIDTH + (stages.length - 1) * NODE_GAP;
  const lefts = stages.map((_, index) => 3 + index * (NODE_WIDTH + NODE_GAP));
  const flowKey = `${current}:${stages.join("|")}`;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;
    const scene = new Scene();
    const camera = new OrthographicCamera(0, width, 0, HEIGHT, 0.1, 100);
    camera.position.z = 10;
    const renderer = new WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(width, HEIGHT);
    host.appendChild(renderer.domElement);

    const resources = [];
    stages.forEach((status, index) => {
      const x = lefts[index];
      if (index < stages.length - 1) {
        addArrow(scene, resources, x + NODE_WIDTH + 4, x + NODE_WIDTH + NODE_GAP - 4,
          NODE_Y + NODE_HEIGHT / 2, index + 1 <= activeIndex ? "#65af88" : "#c3cfda");
      }
    });
    renderer.render(scene, camera);
    return () => {
      renderer.domElement.remove();
      renderer.dispose();
      resources.forEach((resource) => resource.dispose());
    };
  }, [flowKey]);

  return (
    <div className="status-flow" aria-label="岗位状态流转">
      <div className="status-flow-canvas"><div ref={hostRef} className="status-flow-track" style={{ width }}>
        {stages.map((status, index) => {
          const visited = index <= activeIndex;
          const available = current === "scored" && status === "greeting_ready" && !belowThreshold;
          const neutral = status.startsWith("ended_");
          const nodeLabel = neutral ? "已结束" : statusInfo(status).label;
          return <button key={status} type="button" disabled={!visited && !available}
            className={`job-status status-flow-node ${statusInfo(status).className} ${neutral ? "neutral" : ""} ${belowThreshold && status === "scored" ? "muted" : ""} ${available ? "available" : ""}`}
            style={{ left: lefts[index], top: NODE_Y, width: NODE_WIDTH, height: NODE_HEIGHT }}
            title={nodeLabel}
            aria-label={nodeLabel}
            aria-current={status === current ? "step" : undefined}
            aria-pressed={selected === status} onClick={() => onSelect(status)}>
            {nodeLabel}
          </button>;
        })}
      </div></div>
    </div>
  );
}
