/**
 * 採点軸別平均のレーダーチャート。
 *
 * 外部ライブラリは使わず、インラインSVGを組み立てる。5軸固定ではなく、
 * 渡された軸数から多角形を導出する(Criterionの個数はモデル上可変のため)。
 * 各軸のスケールはそのCriterionのmax_scoreを使う。
 *
 * 座標計算(radarPoints)と描画(renderRadarChart)を分けてあり、座標計算は
 * DOMに触らない純粋関数。
 */
(function (global) {
  const SVG_NS = "http://www.w3.org/2000/svg";

  // リング(目盛り)の本数
  const RING_COUNT = 4;
  // ラベルを折り返す文字数の目安。日本語の軸名が長くてもSVGからはみ出さないようにする。
  const MAX_LABEL_CHARS = 8;

  function createSvgElement(name, attributes) {
    const element = document.createElementNS(SVG_NS, name);
    Object.entries(attributes || {}).forEach(([key, value]) => {
      element.setAttribute(key, String(value));
    });
    return element;
  }

  /**
   * 各軸の頂点座標を返す純粋関数。
   *
   * 12時方向を始点として時計回りに等間隔で軸を配置する。
   * values[i] / maxes[i] を半径の比率として使う(maxが0以下の軸は中心に置く)。
   */
  function radarPoints(values, maxes, cx, cy, radius) {
    const count = values.length;
    return values.map((value, index) => {
      const max = maxes[index];
      const ratio = max > 0 ? Math.min(Math.max(value / max, 0), 1) : 0;
      const angle = (2 * Math.PI * index) / count - Math.PI / 2;
      return {
        x: cx + radius * ratio * Math.cos(angle),
        y: cy + radius * ratio * Math.sin(angle),
        angle: angle,
      };
    });
  }

  /** 全軸を最大値にしたときの外周座標(リング・軸線・ラベル位置の基準)。 */
  function outlinePoints(count, cx, cy, radius) {
    return radarPoints(new Array(count).fill(1), new Array(count).fill(1), cx, cy, radius);
  }

  function toPolygonPoints(points) {
    return points.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ");
  }

  function wrapLabel(text) {
    if (text.length <= MAX_LABEL_CHARS) return [text];
    const lines = [];
    for (let i = 0; i < text.length; i += MAX_LABEL_CHARS) {
      lines.push(text.slice(i, i + MAX_LABEL_CHARS));
    }
    return lines;
  }

  function appendLabel(svg, point, cx, cy, text) {
    const lines = wrapLabel(text);
    const lineHeight = 13;

    // 左右は文字の寄せ方で、上下は行数ぶんの位置調整ではみ出しを防ぐ
    let anchor = "middle";
    if (point.x > cx + 1) anchor = "start";
    else if (point.x < cx - 1) anchor = "end";

    let dy = 0;
    if (point.y < cy - 1) dy = -(lines.length - 1) * lineHeight;
    else if (point.y > cy + 1) dy = lineHeight * 0.4;

    const label = createSvgElement("text", {
      x: point.x.toFixed(2),
      y: (point.y + dy).toFixed(2),
      "text-anchor": anchor,
      class: "radar-label",
    });
    lines.forEach((line, index) => {
      const tspan = createSvgElement("tspan", {
        x: point.x.toFixed(2),
        dy: index === 0 ? 0 : lineHeight,
      });
      tspan.textContent = line;
      label.appendChild(tspan);
    });
    svg.appendChild(label);
  }

  /**
   * criterionAverages: [{name, average, max_score}, ...]
   * 戻り値: 生成したsvg要素(containerへ追加済み)
   */
  function renderRadarChart(container, criterionAverages, options) {
    const settings = Object.assign({ size: 260, padding: 54 }, options || {});
    const size = settings.size;
    const cx = size / 2;
    const cy = size / 2;
    const radius = size / 2 - settings.padding;

    container.innerHTML = "";
    if (!criterionAverages || criterionAverages.length < 3) {
      // 3軸未満では多角形にならないため描画しない
      return null;
    }

    const svg = createSvgElement("svg", {
      viewBox: `0 0 ${size} ${size}`,
      class: "radar-chart",
      role: "img",
    });

    const count = criterionAverages.length;
    const outline = outlinePoints(count, cx, cy, radius);

    // 目盛りリング
    for (let ring = 1; ring <= RING_COUNT; ring += 1) {
      const ringPoints = outlinePoints(count, cx, cy, (radius * ring) / RING_COUNT);
      svg.appendChild(
        createSvgElement("polygon", {
          points: toPolygonPoints(ringPoints),
          class: "radar-ring",
        })
      );
    }

    // 軸線
    outline.forEach((point) => {
      svg.appendChild(
        createSvgElement("line", {
          x1: cx,
          y1: cy,
          x2: point.x.toFixed(2),
          y2: point.y.toFixed(2),
          class: "radar-axis",
        })
      );
    });

    // データ多角形
    const values = criterionAverages.map((c) => c.average);
    const maxes = criterionAverages.map((c) => c.max_score);
    const dataPoints = radarPoints(values, maxes, cx, cy, radius);
    svg.appendChild(
      createSvgElement("polygon", {
        points: toPolygonPoints(dataPoints),
        class: "radar-area",
      })
    );

    dataPoints.forEach((point) => {
      svg.appendChild(
        createSvgElement("circle", {
          cx: point.x.toFixed(2),
          cy: point.y.toFixed(2),
          r: 3,
          class: "radar-dot",
        })
      );
    });

    // 軸ラベル(名前と平均値)
    outline.forEach((point, index) => {
      const criterion = criterionAverages[index];
      const labelPoint = {
        x: cx + (point.x - cx) * 1.16,
        y: cy + (point.y - cy) * 1.16,
      };
      appendLabel(svg, labelPoint, cx, cy, `${criterion.name} ${criterion.average}`);
    });

    const title = createSvgElement("title", {});
    title.textContent = criterionAverages
      .map((c) => `${c.name}: ${c.average}/${c.max_score}`)
      .join(", ");
    svg.appendChild(title);

    container.appendChild(svg);
    return svg;
  }

  global.RadarChart = {
    radarPoints: radarPoints,
    wrapLabel: wrapLabel,
    render: renderRadarChart,
  };
})(window);
