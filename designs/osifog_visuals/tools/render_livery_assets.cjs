"use strict";

const fs = require("fs");
const path = require("path");
const sharp = require("sharp");

const ROOT = path.resolve(__dirname, "..");
const REPO = path.resolve(ROOT, "..", "..");
const GENERATED = path.join(ROOT, "assets", "generated");
const SOURCE = path.join(ROOT, "assets", "source");

fs.mkdirSync(GENERATED, { recursive: true });
fs.mkdirSync(SOURCE, { recursive: true });

const COLORS = {
  void: "#050505",
  deep: "#030305",
  surface: "#0A0A0A",
  titanium: "#E0E0E0",
  violet: "#7F00FF",
  cyan: "#00F0FF",
};

const GLYPH_PATH = "M5 5 V19 H13 M15 5 H19 V11 H15 V19 H19";
const CLEAN_GLYPH_PATH = "M5 5 V19 H13 M15 5 H19 V11 H15 V19 H19";
// Cinzel 600, SYSTEMS, tracking 260 font units. Extracted from the self-hosted
// ATLAS font asset and frozen as outlines so OpenRocket cannot substitute it.
const CINZEL_SYSTEMS_PATH = "M268 714Q287 714 313 712Q339 710 365.5 706.5Q392 703 413 699.5Q434 696 442 692L434 565H424Q424 616 387.5 646Q351 676 293 676Q235 676 200.5 646Q166 616 165 573Q163 548 179.5 524.5Q196 501 224 481L417 338Q458 311 477 272.5Q496 234 494 186Q491 94 427.5 40Q364 -14 255 -14Q218 -14 179.5 -7.5Q141 -1 109 12.5Q77 26 58 45Q52 65 53 93.5Q54 122 61 152.5Q68 183 78 204H87Q83 153 104 111.5Q125 70 165.5 46.5Q206 23 261 24Q323 26 360.5 59Q398 92 398 144Q398 174 382 198.5Q366 223 332 243L151 380Q105 410 85.5 452.5Q66 495 69 541Q72 589 96 628.5Q120 668 163.5 691Q207 714 268 714ZM443 704 442 683H338V704ZM996 699 1198 329 1089 308 853 699ZM1206 322V0H1084V322ZM1373 699H1445L1197 302 1151 312ZM1332 628Q1340 642 1336 656.5Q1332 671 1321.5 680.5Q1311 690 1297 690Q1297 690 1291 690Q1285 690 1285 690V700H1518V690H1504Q1473 690 1445.5 672Q1418 654 1403 631ZM1035 628 894 631Q880 654 853 672Q826 690 794 690H780V700H1083V690Q1083 690 1076.5 690Q1070 690 1070 690Q1048 690 1036.5 669.5Q1025 649 1035 628ZM1087 73V0H1007V10Q1007 10 1013 10Q1019 10 1020 10Q1046 10 1064.5 28.5Q1083 47 1084 73ZM1203 73H1206Q1206 47 1225 28.5Q1244 10 1270 10Q1270 10 1276 10Q1282 10 1282 10V0H1203ZM2021 714Q2040 714 2066 712Q2092 710 2118.5 706.5Q2145 703 2166 699.5Q2187 696 2195 692L2187 565H2177Q2177 616 2140.5 646Q2104 676 2046 676Q1988 676 1953.5 646Q1919 616 1918 573Q1916 548 1932.5 524.5Q1949 501 1977 481L2170 338Q2211 311 2230 272.5Q2249 234 2247 186Q2244 94 2180.5 40Q2117 -14 2008 -14Q1971 -14 1932.5 -7.5Q1894 -1 1862 12.5Q1830 26 1811 45Q1805 65 1806 93.5Q1807 122 1814 152.5Q1821 183 1831 204H1840Q1836 153 1857 111.5Q1878 70 1918.5 46.5Q1959 23 2014 24Q2076 26 2113.5 59Q2151 92 2151 144Q2151 174 2135 198.5Q2119 223 2085 243L1904 380Q1858 410 1838.5 452.5Q1819 495 1822 541Q1825 589 1849 628.5Q1873 668 1916.5 691Q1960 714 2021 714ZM2196 704 2195 683H2091V704ZM2944 697V0H2822V697ZM3192 701V663H2573V701ZM3192 666V561L3182 562V575Q3182 614 3158 638Q3134 662 3095 663V666ZM3192 719V690L3070 701Q3092 701 3116.5 704Q3141 707 3162 711Q3183 715 3192 719ZM2825 73V0H2745V10Q2745 10 2751.5 10Q2758 10 2758 10Q2784 10 2802.5 28.5Q2821 47 2822 73ZM2941 73H2944Q2945 47 2963.5 28.5Q2982 10 3008 10Q3008 10 3014.5 10Q3021 10 3021 10V0H2941ZM2670 666V663Q2631 662 2607 638Q2583 614 2583 575V562L2573 561V666ZM2573 719Q2583 715 2603.5 711Q2624 707 2649 704Q2674 701 2695 701L2573 690ZM3714 700V0H3592V700ZM3979 39 3996 0H3711V39ZM3941 364V326H3711V364ZM3988 700V661H3711V700ZM4049 190 3999 0H3812L3848 39Q3899 39 3936 57.5Q3973 76 3998 110.5Q4023 145 4039 190ZM3941 328V242H3931V253Q3931 284 3910 305Q3889 326 3858 326V328ZM3941 448V362H3858V364Q3889 365 3910 386Q3931 407 3931 438V448ZM3988 664V560H3978V574Q3978 612 3954 636.5Q3930 661 3890 662V664ZM3988 718V689L3866 700Q3888 700 3912.5 703Q3937 706 3958 710Q3979 714 3988 718ZM3595 73V0H3515V10Q3515 10 3521.5 10Q3528 10 3528 10Q3554 10 3572.5 28.5Q3591 47 3592 73ZM3595 627H3592Q3591 653 3572.5 671.5Q3554 690 3528 690Q3528 690 3521.5 690Q3515 690 3515 690V700H3595ZM5107 714 5115 619 4833 90Q4833 90 4824 72.5Q4815 55 4806 30Q4797 5 4796 -18H4786L4755 61ZM4450 73V0H4339V10Q4340 10 4347.5 10Q4355 10 4355 10Q4382 10 4403 26.5Q4424 43 4428 73ZM4489 57Q4489 56 4489 55Q4489 54 4489 52Q4489 36 4500.5 22.5Q4512 9 4528 9H4543V0H4482V57ZM4508 714H4518L4548 627 4483 0H4419ZM4518 714 4832 148 4786 -18 4494 519ZM5116 714 5208 0H5075L5023 508 5107 714ZM5177 73H5199Q5204 43 5224.5 26.5Q5245 10 5272 10Q5272 10 5279.5 10Q5287 10 5288 10V0H5177ZM5069 57H5076V0H5015V9H5030Q5047 9 5058 22.5Q5069 36 5069 52Q5069 54 5069 55Q5069 56 5069 57ZM5814 714Q5833 714 5859 712Q5885 710 5911.5 706.5Q5938 703 5959 699.5Q5980 696 5988 692L5980 565H5970Q5970 616 5933.5 646Q5897 676 5839 676Q5781 676 5746.5 646Q5712 616 5711 573Q5709 548 5725.5 524.5Q5742 501 5770 481L5963 338Q6004 311 6023 272.5Q6042 234 6040 186Q6037 94 5973.5 40Q5910 -14 5801 -14Q5764 -14 5725.5 -7.5Q5687 -1 5655 12.5Q5623 26 5604 45Q5598 65 5599 93.5Q5600 122 5607 152.5Q5614 183 5624 204H5633Q5629 153 5650 111.5Q5671 70 5711.5 46.5Q5752 23 5807 24Q5869 26 5906.5 59Q5944 92 5944 144Q5944 174 5928 198.5Q5912 223 5878 243L5697 380Q5651 410 5631.5 452.5Q5612 495 5615 541Q5618 589 5642 628.5Q5666 668 5709.5 691Q5753 714 5814 714ZM5989 704 5988 683H5884V704Z";
const CINZEL_SYSTEMS_WIDTH = 6090;

function fmt(value) {
  return Number(value.toFixed(2));
}

function contourPaths(width, height, count, amplitude, colorMode = "mixed") {
  const paths = [];
  const samples = 96;
  for (let line = -2; line < count + 2; line += 1) {
    const base = (line + 0.5) * height / count;
    const phase = line * 0.71;
    const points = [];
    for (let index = 0; index <= samples; index += 1) {
      const x = width * index / samples;
      const y = base
        + amplitude * Math.sin((Math.PI * 2 * x / width) + phase)
        + amplitude * 0.38 * Math.sin((Math.PI * 4 * x / width) - phase * 0.63)
        + amplitude * 0.16 * Math.sin((Math.PI * 8 * x / width) + phase * 1.37);
      points.push(`${index === 0 ? "M" : "L"}${fmt(x)} ${fmt(y)}`);
    }
    const color = colorMode === "violet"
      ? COLORS.violet
      : colorMode === "cyan"
        ? COLORS.cyan
        : line % 3 === 0
          ? COLORS.cyan
          : COLORS.violet;
    const opacity = 0.18 + (line % 4) * 0.035;
    paths.push(
      `<path d="${points.join(" ")}" fill="none" stroke="${color}" ` +
      `stroke-opacity="${opacity.toFixed(3)}" stroke-width="2"/>`
    );
  }
  return paths.join("\n");
}

function rocketContourPaths(width, height, stage) {
  const paths = [];
  const count = stage === "sustainer" ? 9 : 11;
  const samples = 72;
  const amplitude = stage === "sustainer" ? 92 : 76;
  for (let line = -1; line <= count; line += 1) {
    const base = (line + 0.5) * height / count;
    const phase = line * 0.83 + (stage === "sustainer" ? 0.4 : 1.2);
    const points = [];
    for (let index = 0; index <= samples; index += 1) {
      const x = width * index / samples;
      const y = base
        + amplitude * Math.sin((Math.PI * 2 * x / width) + phase)
        + amplitude * 0.33 * Math.sin((Math.PI * 4 * x / width) - phase * 0.7)
        + amplitude * 0.12 * Math.sin((Math.PI * 6 * x / width) + phase * 1.4);
      points.push(`${index === 0 ? "M" : "L"}${fmt(x)} ${fmt(y)}`);
    }
    const color = line % 4 === 0 ? COLORS.cyan : COLORS.violet;
    const d = points.join(" ");
    paths.push(
      `<path d="${d}" fill="none" stroke="#000814" stroke-opacity="0.88" ` +
      `stroke-width="30" stroke-linecap="round"/>`,
      `<path d="${d}" fill="none" stroke="${color}" stroke-opacity="0.72" ` +
      `stroke-width="14" stroke-linecap="round"/>`
    );
  }
  return paths.join("\n");
}

function continuousContourPaths(width, height, globalStartM, stageLengthM) {
  const paths = [];
  const count = 11;
  const samples = 96;
  const amplitude = 82;
  for (let line = -1; line <= count; line += 1) {
    const base = (line + 0.5) * height / count;
    const phase = line * 0.83;
    const points = [];
    for (let index = 0; index <= samples; index += 1) {
      const x = width * index / samples;
      const globalM = globalStartM + stageLengthM * index / samples;
      const y = base
        + amplitude * Math.sin((Math.PI * 2 * globalM / 1.16) + phase)
        + amplitude * 0.31 * Math.sin((Math.PI * 2 * globalM / 0.57) - phase * 0.7)
        + amplitude * 0.11 * Math.sin((Math.PI * 2 * globalM / 0.29) + phase * 1.4);
      points.push(`${index === 0 ? "M" : "L"}${fmt(x)} ${fmt(y)}`);
    }
    const color = line % 4 === 0 ? COLORS.cyan : COLORS.violet;
    const d = points.join(" ");
    paths.push(
      `<path d="${d}" fill="none" stroke="#000814" stroke-opacity="0.9" ` +
      `stroke-width="30" stroke-linecap="butt"/>`,
      `<path d="${d}" fill="none" stroke="${color}" stroke-opacity="0.76" ` +
      `stroke-width="14" stroke-linecap="butt"/>`
    );
  }
  return paths.join("\n");
}

function terrainValue(globalM, circumferencePhase) {
  const periodicDistance = (phase, center) => 1 - Math.cos(phase - center);
  const peakFore = 1.42 * Math.exp(
    -Math.pow((globalM - 0.34) / 0.22, 2)
    -periodicDistance(circumferencePhase, 1.08) / 0.24
  );
  const peakAft = 1.18 * Math.exp(
    -Math.pow((globalM - 1.27) / 0.3, 2)
    -periodicDistance(circumferencePhase, 4.18) / 0.3
  );
  const basin = -0.88 * Math.exp(
    -Math.pow((globalM - 0.88) / 0.27, 2)
    -periodicDistance(circumferencePhase, 2.86) / 0.38
  );
  const ridge = 0.27 * Math.sin(
    Math.PI * 2 * globalM / 0.73 + 1.45 * Math.sin(circumferencePhase)
  );
  const strata = 0.16 * Math.cos(
    circumferencePhase * 3.0 - globalM * 5.4
  );
  return peakFore + peakAft + basin + ridge + strata;
}

function topographicFieldPaths(
  width,
  height,
  globalStartM,
  stageLengthM,
  style = null
) {
  const xCells = Math.round(stageLengthM * 100);
  const yCells = 96;
  const levels = Array.from({ length: 17 }, (_, index) => -0.78 + index * 0.13);
  const edgeCorners = [[0, 1], [1, 2], [3, 2], [0, 3]];
  const basicCases = {
    1: [[3, 0]], 2: [[0, 1]], 3: [[3, 1]], 4: [[1, 2]],
    6: [[0, 2]], 7: [[3, 2]], 8: [[2, 3]], 9: [[0, 2]],
    11: [[1, 2]], 12: [[1, 3]], 13: [[0, 1]], 14: [[3, 0]],
  };
  const values = [];
  for (let xi = 0; xi <= xCells; xi += 1) {
    const globalM = globalStartM + stageLengthM * xi / xCells;
    const column = [];
    for (let yi = 0; yi <= yCells; yi += 1) {
      const phase = Math.PI * 2 * yi / yCells;
      column.push(terrainValue(globalM, phase));
    }
    values.push(column);
  }

  const output = [];
  levels.forEach((level, levelIndex) => {
    const segments = [];
    for (let xi = 0; xi < xCells; xi += 1) {
      for (let yi = 0; yi < yCells; yi += 1) {
        const corners = [
          { x: xi, y: yi, value: values[xi][yi] },
          { x: xi + 1, y: yi, value: values[xi + 1][yi] },
          { x: xi + 1, y: yi + 1, value: values[xi + 1][yi + 1] },
          { x: xi, y: yi + 1, value: values[xi][yi + 1] },
        ];
        const code = corners.reduce(
          (sum, corner, index) => sum + (corner.value >= level ? 1 << index : 0),
          0
        );
        if (code === 0 || code === 15) continue;
        let pairs = basicCases[code];
        if (code === 5 || code === 10) {
          const center = corners.reduce((sum, corner) => sum + corner.value, 0) / 4;
          pairs = code === 5
            ? (center >= level ? [[3, 2], [0, 1]] : [[3, 0], [2, 1]])
            : (center >= level ? [[3, 0], [2, 1]] : [[3, 2], [0, 1]]);
        }
        function pointOnEdge(edge) {
          const [aIndex, bIndex] = edgeCorners[edge];
          const a = corners[aIndex];
          const b = corners[bIndex];
          const denominator = b.value - a.value;
          const t = Math.abs(denominator) < 1e-9
            ? 0.5
            : Math.max(0, Math.min(1, (level - a.value) / denominator));
          return {
            x: width * (a.x + (b.x - a.x) * t) / xCells,
            y: height * (a.y + (b.y - a.y) * t) / yCells,
          };
        }
        pairs.forEach(([edgeA, edgeB]) => {
          const a = pointOnEdge(edgeA);
          const b = pointOnEdge(edgeB);
          segments.push(`M${fmt(a.x)} ${fmt(a.y)}L${fmt(b.x)} ${fmt(b.y)}`);
        });
      }
    }
    const isSignal = levelIndex === 5 || levelIndex === 12;
    const isMajor = levelIndex % 4 === 0;
    const signal = levelIndex === 5
      ? (style?.cyan || COLORS.cyan)
      : levelIndex === 12
        ? (style?.violet || COLORS.violet)
        : style
          ? (isMajor ? style.majorContour : style.minorContour)
          : "#285A8A";
    const widthPx = style
      ? (isSignal ? style.signalWidth : isMajor ? style.majorWidth : style.minorWidth)
      : (isSignal ? 13 : 8);
    const opacity = style
      ? (isSignal ? style.signalOpacity : isMajor ? style.majorOpacity : style.minorOpacity)
      : (isSignal ? 0.78 : 0.43);
    output.push(
      `<path d="${segments.join("")}" fill="none" stroke="#000814" ` +
      `stroke-opacity="0.86" stroke-width="${widthPx + 12}" stroke-linecap="butt"/>`,
      `<path d="${segments.join("")}" fill="none" stroke="${signal}" ` +
      `stroke-opacity="${opacity}" stroke-width="${widthPx}" stroke-linecap="butt"/>`
    );
  });
  return output.join("\n");
}

function railGlyphMarkup({ id, x, y, width, height }) {
  const sx = width / 24;
  const sy = height / 24;
  return `
    <g transform="translate(${fmt(x)} ${fmt(y)}) scale(${sx} ${sy})">
      <path d="${GLYPH_PATH}" fill="none" stroke="#020202" stroke-width="4.4"
        stroke-linecap="square" stroke-linejoin="miter"/>
      <rect x="10.4" y="16.4" width="3.2" height="3.2" fill="#020202"/>
      <path d="${GLYPH_PATH}" fill="none" stroke="${COLORS.titanium}" stroke-width="3"
        stroke-linecap="square" stroke-linejoin="miter"/>
      <rect x="11" y="17" width="2" height="2" fill="${COLORS.titanium}"/>
    </g>
    <path d="M${fmt(x - 34)} ${fmt(y)} V${fmt(y + height)}"
      stroke="${COLORS.cyan}" stroke-width="14" stroke-linecap="square"/>
    <path d="M${fmt(x + width + 34)} ${fmt(y)} V${fmt(y + height)}"
      stroke="${COLORS.violet}" stroke-width="14" stroke-linecap="square"/>`;
}

function systemsLockupMarkup({ x, y, width, height, id }) {
  const cut = Math.min(30, height * 0.13);
  const glyphSize = height * 0.66;
  const glyphX = x + height * 0.28;
  const glyphY = y + (height - glyphSize) * 0.5;
  const glyphScale = glyphSize / 24;
  const textX = glyphX + glyphSize + height * 0.24;
  const textY = y + height * 0.57;
  const textSize = height * 0.225;
  const panelPath = [
    `M${fmt(x + cut)} ${fmt(y)}`,
    `H${fmt(x + width - cut)}`,
    `L${fmt(x + width)} ${fmt(y + cut)}`,
    `V${fmt(y + height - cut)}`,
    `L${fmt(x + width - cut)} ${fmt(y + height)}`,
    `H${fmt(x + cut)}`,
    `L${fmt(x)} ${fmt(y + height - cut)}`,
    `V${fmt(y + cut)} Z`,
  ].join(" ");
  return `
    <path id="${id}-panel" d="${panelPath}" fill="${COLORS.deep}"
      fill-opacity="0.96" stroke="${COLORS.titanium}" stroke-opacity="0.2"
      stroke-width="5"/>
    <path d="M${fmt(x + 18)} ${fmt(y + cut)}
      V${fmt(y + height - cut)}" stroke="${COLORS.cyan}" stroke-width="12"/>
    <path d="M${fmt(x + width - 18)} ${fmt(y + cut)}
      V${fmt(y + height - cut)}" stroke="${COLORS.violet}" stroke-width="12"/>
    <g transform="translate(${fmt(glyphX)} ${fmt(glyphY)})
      scale(${fmt(glyphScale)} ${fmt(glyphScale)})">
      <path d="${GLYPH_PATH}" fill="none" stroke="#020202" stroke-width="4.3"
        stroke-linecap="square" stroke-linejoin="miter"/>
      <path d="${GLYPH_PATH}" fill="none" stroke="${COLORS.titanium}" stroke-width="3"
        stroke-linecap="square" stroke-linejoin="miter"/>
      <rect x="11" y="17" width="2" height="2" fill="${COLORS.titanium}"/>
    </g>
    <text x="${fmt(textX)}" y="${fmt(textY)}"
      fill="${COLORS.titanium}" font-family="Inter, Arial, sans-serif"
      font-size="${fmt(textSize)}" font-weight="700"
      letter-spacing="${fmt(textSize * 0.16)}">SYSTEMS</text>
    <path d="M${fmt(textX)} ${fmt(y + height * 0.68)}
      H${fmt(x + width - height * 0.24)}"
      stroke="${COLORS.titanium}" stroke-opacity="0.32" stroke-width="4"/>
    <path d="M${fmt(textX)} ${fmt(y + height * 0.68)}
      H${fmt(textX + (width - (textX - x) - height * 0.24) * 0.32)}"
      stroke="${COLORS.cyan}" stroke-width="4"/>`;
}

function systemsLockupSvg() {
  const width = 1600;
  const height = 500;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"
    viewBox="0 0 ${width} ${height}">
    ${systemsLockupMarkup({
      x: 24,
      y: 24,
      width: width - 48,
      height: height - 48,
      id: "l2-systems-v3",
    })}
  </svg>`;
}

function terrainSystemsLockupMarkup({ x, y, width, height, id }) {
  const cut = Math.min(30, height * 0.13);
  const glyphSize = height * 0.7;
  const glyphX = x + height * 0.25;
  const glyphY = y + (height - glyphSize) * 0.5;
  const glyphScale = glyphSize / 24;
  const textX = glyphX + glyphSize + height * 0.22;
  const textY = y + height * 0.57;
  const textSize = height * 0.22;
  const panelPath = [
    `M${fmt(x + cut)} ${fmt(y)}`, `H${fmt(x + width - cut)}`,
    `L${fmt(x + width)} ${fmt(y + cut)}`, `V${fmt(y + height - cut)}`,
    `L${fmt(x + width - cut)} ${fmt(y + height)}`, `H${fmt(x + cut)}`,
    `L${fmt(x)} ${fmt(y + height - cut)}`, `V${fmt(y + cut)} Z`,
  ].join(" ");
  const maskId = `${id}-glyph-mask`;
  return `
    <defs>
      <mask id="${maskId}" maskUnits="userSpaceOnUse"
        x="${fmt(x)}" y="${fmt(y)}" width="${fmt(width)}" height="${fmt(height)}">
        <rect x="${fmt(x)}" y="${fmt(y)}" width="${fmt(width)}"
          height="${fmt(height)}" fill="black"/>
        <g transform="translate(${fmt(glyphX)} ${fmt(glyphY)})
          scale(${fmt(glyphScale)} ${fmt(glyphScale)})">
          <path d="${GLYPH_PATH}" fill="none" stroke="white" stroke-width="3"
            stroke-linecap="square" stroke-linejoin="miter"/>
          <rect x="11" y="17" width="2" height="2" fill="white"/>
        </g>
      </mask>
    </defs>
    <path d="${panelPath}" fill="#03050A" fill-opacity="0.97"
      stroke="${COLORS.titanium}" stroke-opacity="0.2" stroke-width="5"/>
    <path d="M${fmt(x + 18)} ${fmt(y + cut)} V${fmt(y + height - cut)}"
      stroke="${COLORS.cyan}" stroke-width="12"/>
    <path d="M${fmt(x + width - 18)} ${fmt(y + cut)} V${fmt(y + height - cut)}"
      stroke="${COLORS.violet}" stroke-width="12"/>
    <g transform="translate(${fmt(glyphX)} ${fmt(glyphY)})
      scale(${fmt(glyphScale)} ${fmt(glyphScale)})">
      <path d="${GLYPH_PATH}" fill="none" stroke="${COLORS.titanium}" stroke-width="3"
        stroke-linecap="square" stroke-linejoin="miter"/>
      <rect x="11" y="17" width="2" height="2" fill="${COLORS.titanium}"/>
    </g>
    <g mask="url(#${maskId})">
      <path d="M${fmt(glyphX - 20)} ${fmt(glyphY + glyphSize * 0.3)}
        C${fmt(glyphX + glyphSize * 0.25)} ${fmt(glyphY + glyphSize * 0.08)}
         ${fmt(glyphX + glyphSize * 0.7)} ${fmt(glyphY + glyphSize * 0.55)}
         ${fmt(glyphX + glyphSize + 20)} ${fmt(glyphY + glyphSize * 0.24)}"
        fill="none" stroke="#35475F" stroke-width="9"/>
      <path d="M${fmt(glyphX - 20)} ${fmt(glyphY + glyphSize * 0.58)}
        C${fmt(glyphX + glyphSize * 0.28)} ${fmt(glyphY + glyphSize * 0.35)}
         ${fmt(glyphX + glyphSize * 0.62)} ${fmt(glyphY + glyphSize * 0.82)}
         ${fmt(glyphX + glyphSize + 20)} ${fmt(glyphY + glyphSize * 0.52)}"
        fill="none" stroke="#53657A" stroke-width="8"/>
      <path d="M${fmt(glyphX - 20)} ${fmt(glyphY + glyphSize * 0.8)}
        C${fmt(glyphX + glyphSize * 0.3)} ${fmt(glyphY + glyphSize * 0.62)}
         ${fmt(glyphX + glyphSize * 0.7)} ${fmt(glyphY + glyphSize * 1.02)}
         ${fmt(glyphX + glyphSize + 20)} ${fmt(glyphY + glyphSize * 0.74)}"
        fill="none" stroke="${COLORS.cyan}" stroke-opacity="0.45" stroke-width="7"/>
    </g>
    <text x="${fmt(textX)}" y="${fmt(textY)}"
      fill="${COLORS.titanium}" font-family="Inter, Arial, sans-serif"
      font-size="${fmt(textSize)}" font-weight="700"
      letter-spacing="${fmt(textSize * 0.16)}">SYSTEMS</text>
    <path d="M${fmt(textX)} ${fmt(y + height * 0.68)}
      H${fmt(x + width - height * 0.23)}"
      stroke="${COLORS.titanium}" stroke-opacity="0.3" stroke-width="4"/>
    <path d="M${fmt(textX)} ${fmt(y + height * 0.68)}
      H${fmt(textX + (width - (textX - x) - height * 0.23) * 0.34)}"
      stroke="${COLORS.cyan}" stroke-width="4"/>`;
}

function terrainSystemsLockupSvg() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="500"
    viewBox="0 0 1600 500">
    ${terrainSystemsLockupMarkup({
      x: 24, y: 24, width: 1552, height: 452, id: "l2-systems-terrain-v4",
    })}
  </svg>`;
}

function vectorSystemsWordmarkMarkup({ x, y, width, height }) {
  const letters = [
    "M10 3C8 1 4 1 2.5 3C1 5 2 8 5 8H7C10 8 11 11 9.5 14C8 16 4 17 2 15",
    "M2 2L6 8L10 2M6 8V16",
    "M1 2H11M6 2V16",
    "M10 2H2V16H10M2 9H8",
    "M2 16V2L6 8L10 2V16",
    "M10 3C8 1 4 1 2.5 3C1 5 2 8 5 8H7C10 8 11 11 9.5 14C8 16 4 17 2 15",
    "M10 3C8 1 4 1 2.5 3C1 5 2 8 5 8H7C10 8 11 11 9.5 14C8 16 4 17 2 15",
  ];
  const letterWidth = 12;
  const gap = 5;
  const nativeWidth = letters.length * letterWidth + (letters.length - 1) * gap;
  const scale = Math.min(width / nativeWidth, height / 18);
  const renderedWidth = nativeWidth * scale;
  const renderedHeight = 18 * scale;
  const startX = x + (width - renderedWidth) * 0.5;
  const startY = y + (height - renderedHeight) * 0.5;
  return letters.map((pathData, index) => `
    <path d="${pathData}"
      transform="translate(${fmt(startX + index * (letterWidth + gap) * scale)}
        ${fmt(startY)}) scale(${fmt(scale)} ${fmt(scale)})"
      fill="none" stroke="${COLORS.titanium}" stroke-width="2.35"
      stroke-linecap="square" stroke-linejoin="miter"/>`).join("\n");
}

function cleanSystemsLockupMarkup({ x, y, width, height, id }) {
  const cut = Math.min(28, height * 0.12);
  const glyphSize = height * 0.7;
  const glyphX = x + height * 0.24;
  const glyphY = y + (height - glyphSize) * 0.5;
  const glyphScale = glyphSize / 24;
  const wordX = glyphX + glyphSize + height * 0.2;
  const wordWidth = x + width - height * 0.25 - wordX;
  const wordY = y + height * 0.25;
  const wordHeight = height * 0.48;
  const panelPath = [
    `M${fmt(x + cut)} ${fmt(y)}`, `H${fmt(x + width - cut)}`,
    `L${fmt(x + width)} ${fmt(y + cut)}`, `V${fmt(y + height - cut)}`,
    `L${fmt(x + width - cut)} ${fmt(y + height)}`, `H${fmt(x + cut)}`,
    `L${fmt(x)} ${fmt(y + height - cut)}`, `V${fmt(y + cut)} Z`,
  ].join(" ");
  const maskId = `${id}-clean-glyph-mask`;
  return `
    <defs>
      <mask id="${maskId}" maskUnits="userSpaceOnUse"
        x="${fmt(x)}" y="${fmt(y)}" width="${fmt(width)}" height="${fmt(height)}">
        <rect x="${fmt(x)}" y="${fmt(y)}" width="${fmt(width)}"
          height="${fmt(height)}" fill="black"/>
        <g transform="translate(${fmt(glyphX)} ${fmt(glyphY)})
          scale(${fmt(glyphScale)} ${fmt(glyphScale)})">
          <path d="${CLEAN_GLYPH_PATH}" fill="none" stroke="white" stroke-width="3"
            stroke-linecap="square" stroke-linejoin="miter"/>
        </g>
      </mask>
    </defs>
    <path d="${panelPath}" fill="#03050A" fill-opacity="0.97"
      stroke="${COLORS.titanium}" stroke-opacity="0.18" stroke-width="5"/>
    <path d="M${fmt(x + 18)} ${fmt(y + cut)} V${fmt(y + height - cut)}"
      stroke="${COLORS.cyan}" stroke-width="12"/>
    <path d="M${fmt(x + width - 18)} ${fmt(y + cut)} V${fmt(y + height - cut)}"
      stroke="${COLORS.violet}" stroke-width="12"/>
    <g transform="translate(${fmt(glyphX)} ${fmt(glyphY)})
      scale(${fmt(glyphScale)} ${fmt(glyphScale)})">
      <path d="${CLEAN_GLYPH_PATH}" fill="none" stroke="${COLORS.titanium}"
        stroke-width="3" stroke-linecap="square" stroke-linejoin="miter"/>
    </g>
    <g mask="url(#${maskId})">
      <path d="M${fmt(glyphX - 20)} ${fmt(glyphY + glyphSize * 0.28)}
        C${fmt(glyphX + glyphSize * 0.25)} ${fmt(glyphY + glyphSize * 0.08)}
         ${fmt(glyphX + glyphSize * 0.68)} ${fmt(glyphY + glyphSize * 0.52)}
         ${fmt(glyphX + glyphSize + 20)} ${fmt(glyphY + glyphSize * 0.22)}"
        fill="none" stroke="#35475F" stroke-width="9"/>
      <path d="M${fmt(glyphX - 20)} ${fmt(glyphY + glyphSize * 0.56)}
        C${fmt(glyphX + glyphSize * 0.26)} ${fmt(glyphY + glyphSize * 0.36)}
         ${fmt(glyphX + glyphSize * 0.64)} ${fmt(glyphY + glyphSize * 0.8)}
         ${fmt(glyphX + glyphSize + 20)} ${fmt(glyphY + glyphSize * 0.5)}"
        fill="none" stroke="#53657A" stroke-width="8"/>
      <path d="M${fmt(glyphX - 20)} ${fmt(glyphY + glyphSize * 0.82)}
        C${fmt(glyphX + glyphSize * 0.28)} ${fmt(glyphY + glyphSize * 0.62)}
         ${fmt(glyphX + glyphSize * 0.7)} ${fmt(glyphY + glyphSize)}
         ${fmt(glyphX + glyphSize + 20)} ${fmt(glyphY + glyphSize * 0.74)}"
        fill="none" stroke="${COLORS.cyan}" stroke-opacity="0.46" stroke-width="7"/>
    </g>
    ${vectorSystemsWordmarkMarkup({
      x: wordX, y: wordY, width: wordWidth, height: wordHeight,
    })}
    <path d="M${fmt(wordX)} ${fmt(y + height * 0.72)}
      H${fmt(x + width - height * 0.25)}"
      stroke="${COLORS.titanium}" stroke-opacity="0.22" stroke-width="4"/>
    <path d="M${fmt(wordX)} ${fmt(y + height * 0.72)}
      H${fmt(wordX + wordWidth * 0.28)}"
      stroke="${COLORS.cyan}" stroke-width="4"/>`;
}

function cleanSystemsLockupSvg() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="500"
    viewBox="0 0 1600 500">
    ${cleanSystemsLockupMarkup({
      x: 24, y: 24, width: 1552, height: 452, id: "l2-systems-clean-v5",
    })}
  </svg>`;
}

function atlasGradeSystemsLockupMarkup({ x, y, width, height, id }) {
  const glyphSize = height * 0.61;
  const glyphX = x + height * 0.19;
  const glyphY = y + (height - glyphSize) * 0.46;
  const glyphScale = glyphSize / 24;
  const wordX = glyphX + glyphSize + height * 0.23;
  const wordRight = x + width - height * 0.19;
  const wordWidth = wordRight - wordX;
  const fontScale = Math.min(wordWidth / CINZEL_SYSTEMS_WIDTH, height * 0.31 / 732);
  const baselineY = y + height * 0.61;
  const ruleY = y + height * 0.73;
  const maskId = `${id}-atlas-grade-glyph`;
  return `
    <defs>
      <linearGradient id="${id}-field-fade" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="#03050A" stop-opacity="0"/>
        <stop offset="0.14" stop-color="#03050A" stop-opacity="0.92"/>
        <stop offset="0.86" stop-color="#03050A" stop-opacity="0.92"/>
        <stop offset="1" stop-color="#03050A" stop-opacity="0"/>
      </linearGradient>
      <linearGradient id="${id}-ivory" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#FFFFFF"/>
        <stop offset="0.48" stop-color="#EDEAE0"/>
        <stop offset="1" stop-color="#C9BCA6"/>
      </linearGradient>
      <linearGradient id="${id}-signal-rule" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="${COLORS.cyan}" stop-opacity="0"/>
        <stop offset="0.18" stop-color="${COLORS.cyan}" stop-opacity="0.72"/>
        <stop offset="0.5" stop-color="#EDEAE0" stop-opacity="0.28"/>
        <stop offset="0.82" stop-color="${COLORS.violet}" stop-opacity="0.72"/>
        <stop offset="1" stop-color="${COLORS.violet}" stop-opacity="0"/>
      </linearGradient>
      <mask id="${maskId}" maskUnits="userSpaceOnUse"
        x="${fmt(x)}" y="${fmt(y)}" width="${fmt(width)}" height="${fmt(height)}">
        <rect x="${fmt(x)}" y="${fmt(y)}" width="${fmt(width)}"
          height="${fmt(height)}" fill="black"/>
        <g transform="translate(${fmt(glyphX)} ${fmt(glyphY)})
          scale(${fmt(glyphScale)} ${fmt(glyphScale)})">
          <path d="${CLEAN_GLYPH_PATH}" fill="none" stroke="white" stroke-width="3"
            stroke-linecap="square" stroke-linejoin="miter"/>
        </g>
      </mask>
    </defs>
    <rect x="${fmt(x)}" y="${fmt(y)}" width="${fmt(width)}" height="${fmt(height)}"
      fill="url(#${id}-field-fade)"/>
    <g transform="translate(${fmt(glyphX)} ${fmt(glyphY)})
      scale(${fmt(glyphScale)} ${fmt(glyphScale)})">
      <path d="${CLEAN_GLYPH_PATH}" fill="none" stroke="url(#${id}-ivory)"
        stroke-width="3" stroke-linecap="square" stroke-linejoin="miter"/>
    </g>
    <g mask="url(#${maskId})" opacity="0.55">
      <path d="M${fmt(glyphX - 18)} ${fmt(glyphY + glyphSize * 0.28)}
        C${fmt(glyphX + glyphSize * 0.2)} ${fmt(glyphY + glyphSize * 0.12)}
         ${fmt(glyphX + glyphSize * 0.7)} ${fmt(glyphY + glyphSize * 0.5)}
         ${fmt(glyphX + glyphSize + 18)} ${fmt(glyphY + glyphSize * 0.25)}"
        fill="none" stroke="#344258" stroke-width="7"/>
      <path d="M${fmt(glyphX - 18)} ${fmt(glyphY + glyphSize * 0.6)}
        C${fmt(glyphX + glyphSize * 0.24)} ${fmt(glyphY + glyphSize * 0.42)}
         ${fmt(glyphX + glyphSize * 0.68)} ${fmt(glyphY + glyphSize * 0.76)}
         ${fmt(glyphX + glyphSize + 18)} ${fmt(glyphY + glyphSize * 0.54)}"
        fill="none" stroke="#5D6570" stroke-width="6"/>
    </g>
    <path d="${CINZEL_SYSTEMS_PATH}"
      transform="translate(${fmt(wordX)} ${fmt(baselineY)})
        scale(${fmt(fontScale)} ${fmt(-fontScale)})"
      fill="url(#${id}-ivory)"/>
    <path d="M${fmt(wordX)} ${fmt(ruleY)} H${fmt(wordRight)}"
      stroke="url(#${id}-signal-rule)" stroke-width="4"/>
    <circle cx="${fmt((wordX + wordRight) * 0.5)}" cy="${fmt(ruleY)}"
      r="5" fill="#EDEAE0" fill-opacity="0.7"/>
    <path d="M${fmt((wordX + wordRight) * 0.5)} ${fmt(ruleY - 13)}
      V${fmt(ruleY + 13)} M${fmt((wordX + wordRight) * 0.5 - 13)} ${fmt(ruleY)}
      H${fmt((wordX + wordRight) * 0.5 + 13)}"
      stroke="#EDEAE0" stroke-opacity="0.35" stroke-width="2"/>`;
}

function atlasGradeSystemsLockupSvg() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="460"
    viewBox="0 0 1800 460">
    ${atlasGradeSystemsLockupMarkup({
      x: 20, y: 20, width: 1760, height: 420, id: "l2-systems-atlas-grade-v6",
    })}
  </svg>`;
}

function glyphMarkup({
  id,
  x,
  y,
  width,
  height,
  fill = COLORS.titanium,
  contour = COLORS.deep,
  outline = COLORS.titanium,
  canvasWidth = 1024,
  canvasHeight = 1024,
}) {
  const sx = width / 24;
  const sy = height / 24;
  const maskId = `${id}-mask`;
  return `
    <defs>
      <mask id="${maskId}" maskUnits="userSpaceOnUse" x="0" y="0"
        width="${canvasWidth}" height="${canvasHeight}">
        <rect width="${canvasWidth}" height="${canvasHeight}" fill="black"/>
        <g transform="translate(${fmt(x)} ${fmt(y)}) scale(${sx} ${sy})">
          <path d="${GLYPH_PATH}" fill="none" stroke="white" stroke-width="3"
                stroke-linecap="square" stroke-linejoin="miter"/>
          <rect x="11" y="17" width="2" height="2" fill="white"/>
        </g>
      </mask>
    </defs>
    <g transform="translate(${fmt(x)} ${fmt(y)}) scale(${sx} ${sy})">
      <path d="${GLYPH_PATH}" fill="none" stroke="${fill}" stroke-width="3"
            stroke-linecap="square" stroke-linejoin="miter"/>
      <rect x="11" y="17" width="2" height="2" fill="${fill}"/>
    </g>
    <g mask="url(#${maskId})" opacity="0.58">
      ${contourPaths(canvasWidth, canvasHeight, 22, 19, contour === COLORS.cyan ? "cyan" : "violet")}
    </g>
    <g transform="translate(${fmt(x)} ${fmt(y)}) scale(${sx} ${sy})">
      <path d="${GLYPH_PATH}" fill="none" stroke="${outline}" stroke-opacity="0.42"
            stroke-width="0.22" stroke-linecap="square" stroke-linejoin="miter"/>
    </g>`;
}

function enhancedGlyphSvg(mode) {
  const dark = mode === "dark";
  const fill = dark ? COLORS.titanium : COLORS.deep;
  const outline = dark ? COLORS.titanium : COLORS.violet;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024"
    viewBox="0 0 1024 1024">
    <defs>
      <linearGradient id="signal" x1="0" y1="1" x2="1" y2="0">
        <stop offset="0" stop-color="${COLORS.cyan}"/>
        <stop offset="1" stop-color="${COLORS.violet}"/>
      </linearGradient>
      <mask id="glyph-mask" maskUnits="userSpaceOnUse" x="0" y="0"
        width="1024" height="1024">
        <rect width="1024" height="1024" fill="black"/>
        <g transform="translate(92 92) scale(35 35)">
          <path d="${GLYPH_PATH}" fill="none" stroke="white" stroke-width="3"
                stroke-linecap="square" stroke-linejoin="miter"/>
          <rect x="11" y="17" width="2" height="2" fill="white"/>
        </g>
      </mask>
    </defs>
    <g transform="translate(92 92) scale(35 35)">
      <path d="${GLYPH_PATH}" fill="none" stroke="${fill}" stroke-width="3"
            stroke-linecap="square" stroke-linejoin="miter"/>
      <rect x="11" y="17" width="2" height="2" fill="${fill}"/>
    </g>
    <g mask="url(#glyph-mask)">
      ${contourPaths(1024, 1024, 24, 17, dark ? "violet" : "cyan")}
    </g>
    <g transform="translate(92 92) scale(35 35)">
      <path d="${GLYPH_PATH}" fill="none" stroke="${outline}" stroke-opacity="0.65"
            stroke-width="0.16" stroke-linecap="square" stroke-linejoin="miter"/>
    </g>
    <rect x="38" y="38" width="948" height="948" rx="54" fill="none"
          stroke="url(#signal)" stroke-width="8" stroke-opacity="0.9"/>
  </svg>`;
}

function wrapSvg(stage, bodyLength, simple = false) {
  const width = 2048;
  const height = 2048;
  const physicalCircumference = Math.PI * 0.164;
  const glyphWidthFraction = simple ? 0.34 : 0.43;
  const glyphHeightFraction = glyphWidthFraction * physicalCircumference / bodyLength;
  const glyphWidth = width * glyphWidthFraction;
  const glyphHeight = height * glyphHeightFraction;
  const glyphX = width * 0.5 - glyphWidth * 0.5;
  const glyphY = height * 0.5 - glyphHeight * 0.5;
  const labelY = Math.min(height - 150, glyphY + glyphHeight + 130);
  const contours = simple ? "" : contourPaths(width, height, 28, 42);
  const stageLabel = stage === "sustainer"
    ? "SUSTAINER // STAGE 00"
    : "BOOSTER // STAGE 01";
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"
    viewBox="0 0 ${width} ${height}">
    ${contours}
    ${glyphMarkup({
      id: `${stage}-${simple ? "simple" : "topographic"}`,
      x: glyphX,
      y: glyphY,
      width: glyphWidth,
      height: glyphHeight,
      canvasWidth: width,
      canvasHeight: height,
    })}
    <text x="${width / 2}" y="${fmt(labelY)}" text-anchor="middle"
      fill="${COLORS.titanium}" fill-opacity="${simple ? "0.92" : "0.78"}"
      font-family="JetBrains Mono, Consolas, monospace" font-size="54"
      font-weight="700" letter-spacing="13">L2 // SYSTEMS</text>
    <text x="${width / 2}" y="${fmt(labelY + 78)}" text-anchor="middle"
      fill="${simple ? COLORS.cyan : COLORS.titanium}"
      fill-opacity="${simple ? "0.85" : "0.48"}"
      font-family="JetBrains Mono, Consolas, monospace" font-size="28"
      font-weight="500" letter-spacing="9">${stageLabel}</text>
    ${simple ? "" : `
    <path d="M128 154 H520" stroke="${COLORS.cyan}" stroke-width="8"/>
    <path d="M1528 154 H1920" stroke="${COLORS.violet}" stroke-width="8"/>
    <text x="128" y="120" fill="${COLORS.cyan}" fill-opacity="0.75"
      font-family="JetBrains Mono, Consolas, monospace" font-size="24"
      letter-spacing="7">PROTOCOL // ONLINE</text>`}
  </svg>`;
}

function wrapV2Svg(stage, bodyLength) {
  const width = 2048;
  const height = 2048;
  const physicalCircumference = Math.PI * 0.164;
  const targetAxialLength = stage === "sustainer" ? 0.16 : 0.17;
  const targetProjectedHeight = 0.118;
  // The decal is rotated 90 degrees in OpenRocket. Source X therefore becomes
  // axial length, while source Y becomes circumferential/projected height.
  const glyphWidth = width * targetAxialLength / bodyLength;
  const glyphHeight = height * targetProjectedHeight / physicalCircumference;
  const glyphX = width * 0.5 - glyphWidth * 0.5;
  const glyphY = height * 0.5 - glyphHeight * 0.5;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"
    viewBox="0 0 ${width} ${height}">
    ${rocketContourPaths(width, height, stage)}
    <rect x="${fmt(glyphX - 82)}" y="${fmt(glyphY - 72)}"
      width="${fmt(glyphWidth + 164)}" height="${fmt(glyphHeight + 144)}"
      rx="36" fill="${COLORS.deep}" fill-opacity="0.94"
      stroke="${COLORS.titanium}" stroke-opacity="0.16" stroke-width="5"/>
    ${railGlyphMarkup({
      id: `${stage}-rail-v2`,
      x: glyphX,
      y: glyphY,
      width: glyphWidth,
      height: glyphHeight,
    })}
  </svg>`;
}

function wrapV3Svg(stage, bodyLength) {
  const width = 2048;
  const height = 2048;
  const physicalCircumference = Math.PI * 0.164;
  const targetAxialLength = stage === "sustainer" ? 0.235 : 0.255;
  const targetProjectedHeight = 0.092;
  const markWidth = width * targetAxialLength / bodyLength;
  const markHeight = height * targetProjectedHeight / physicalCircumference;
  const markX = width * 0.5 - markWidth * 0.5;
  const markY = height * 0.5 - markHeight * 0.5;
  const globalStartM = stage === "sustainer" ? 0 : 0.7;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"
    viewBox="0 0 ${width} ${height}">
    ${continuousContourPaths(width, height, globalStartM, bodyLength)}
    ${systemsLockupMarkup({
      x: markX,
      y: markY,
      width: markWidth,
      height: markHeight,
      id: `${stage}-systems-v3`,
    })}
  </svg>`;
}

function wrapV4Svg(stage, bodyLength) {
  const width = 2048;
  const height = 2048;
  const physicalCircumference = Math.PI * 0.164;
  const targetAxialLength = stage === "sustainer" ? 0.25 : 0.27;
  const targetProjectedHeight = 0.096;
  const markWidth = width * targetAxialLength / bodyLength;
  const markHeight = height * targetProjectedHeight / physicalCircumference;
  const markX = width * 0.5 - markWidth * 0.5;
  const markY = height * 0.5 - markHeight * 0.5;
  const globalStartM = stage === "sustainer" ? 0 : 0.7;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"
    viewBox="0 0 ${width} ${height}">
    ${topographicFieldPaths(width, height, globalStartM, bodyLength)}
    ${terrainSystemsLockupMarkup({
      x: markX, y: markY, width: markWidth, height: markHeight,
      id: `${stage}-terrain-systems-v4`,
    })}
  </svg>`;
}

function wrapV5Svg(stage, bodyLength) {
  const width = 2048;
  const height = 2048;
  const physicalCircumference = Math.PI * 0.164;
  const targetAxialLength = stage === "sustainer" ? 0.25 : 0.27;
  const targetProjectedHeight = 0.092;
  const markWidth = width * targetAxialLength / bodyLength;
  const markHeight = height * targetProjectedHeight / physicalCircumference;
  const markX = width * 0.5 - markWidth * 0.5;
  const markY = height * 0.5 - markHeight * 0.5;
  const globalStartM = stage === "sustainer" ? 0 : 0.7;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"
    viewBox="0 0 ${width} ${height}">
    ${topographicFieldPaths(width, height, globalStartM, bodyLength)}
    ${cleanSystemsLockupMarkup({
      x: markX, y: markY, width: markWidth, height: markHeight,
      id: `${stage}-clean-systems-v5`,
    })}
  </svg>`;
}

function wrapV6Svg(stage, bodyLength) {
  const width = 2048;
  const height = 2048;
  const physicalCircumference = Math.PI * 0.164;
  const globalStartM = stage === "sustainer" ? 0 : 0.7;
  const terrain = topographicFieldPaths(width, height, globalStartM, bodyLength);
  if (stage !== "sustainer") {
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"
      viewBox="0 0 ${width} ${height}">${terrain}</svg>`;
  }
  const targetAxialLength = 0.35;
  const targetProjectedHeight = 0.087;
  const markWidth = width * targetAxialLength / bodyLength;
  const markHeight = height * targetProjectedHeight / physicalCircumference;
  const markX = width * 0.46 - markWidth * 0.5;
  const markY = height * 0.5 - markHeight * 0.5;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"
    viewBox="0 0 ${width} ${height}">
    ${terrain}
    ${atlasGradeSystemsLockupMarkup({
      x: markX, y: markY, width: markWidth, height: markHeight,
      id: "sustainer-atlas-grade-v6",
    })}
  </svg>`;
}

const CELESTIAL = {
  obsidian: "#0B0D12",
  nose: "#07080C",
  coupler: "#141820",
  contour: "#19324F",
  cyan: "#46F0E0",
  ivory: "#EDEAE0",
  violet: "#9B6DFF",
  forwardFin: "#0D555D",
  aftFin: "#2A0C43",
};

function celestialTerrainPaths(width, height, globalStartM, stageLengthM) {
  return topographicFieldPaths(
    width,
    height,
    globalStartM,
    stageLengthM,
    {
      cyan: CELESTIAL.cyan,
      violet: CELESTIAL.violet,
      majorContour: "#587897",
      minorContour: "#35536F",
      signalWidth: 11,
      majorWidth: 8.5,
      minorWidth: 6.5,
      signalOpacity: 0.74,
      majorOpacity: 0.58,
      minorOpacity: 0.46,
    }
  ).replaceAll("#000814", "#05070C");
}

function celestialDatumPath(width, height, globalStartM, stageLengthM) {
  const samples = 128;
  const points = [];
  for (let index = 0; index <= samples; index += 1) {
    const x = width * index / samples;
    const globalM = globalStartM + stageLengthM * index / samples;
    const y = height * (
      0.503
      + 0.038 * Math.sin(Math.PI * 2 * globalM / 1.34 + 0.28)
      + 0.012 * Math.sin(Math.PI * 2 * globalM / 0.47 - 0.62)
    );
    points.push(`${index === 0 ? "M" : "L"}${fmt(x)} ${fmt(y)}`);
  }
  return points.join(" ");
}

function celestialDatumMarkup({
  width,
  height,
  globalStartM,
  stageLengthM,
  id,
  showNode = false,
}) {
  const pathData = celestialDatumPath(
    width, height, globalStartM, stageLengthM
  );
  const nodeGlobalM = 0.58;
  const nodeX = width * (nodeGlobalM - globalStartM) / stageLengthM;
  const nodeY = height * (
    0.503
    + 0.038 * Math.sin(Math.PI * 2 * nodeGlobalM / 1.34 + 0.28)
    + 0.012 * Math.sin(Math.PI * 2 * nodeGlobalM / 0.47 - 0.62)
  );
  return `
    <defs>
      <linearGradient id="${id}-datum" gradientUnits="userSpaceOnUse"
        x1="0" y1="0" x2="${width}" y2="0">
        <stop offset="0" stop-color="${
          globalStartM === 0 ? CELESTIAL.cyan : CELESTIAL.ivory
        }"/>
        <stop offset="${globalStartM === 0 ? "0.68" : "0.18"}"
          stop-color="${CELESTIAL.ivory}"/>
        <stop offset="1" stop-color="${
          globalStartM === 0 ? CELESTIAL.ivory : CELESTIAL.violet
        }"/>
      </linearGradient>
    </defs>
    <path d="${pathData}" fill="none" stroke="#03050A" stroke-opacity="0.94"
      stroke-width="19" stroke-linecap="butt"/>
    <path d="${pathData}" fill="none" stroke="url(#${id}-datum)"
      stroke-opacity="0.93" stroke-width="6" stroke-linecap="butt"/>
    ${showNode ? `
      <circle cx="${fmt(nodeX)}" cy="${fmt(nodeY)}" r="24"
        fill="${CELESTIAL.obsidian}" stroke="${CELESTIAL.ivory}"
        stroke-width="5"/>
      <circle cx="${fmt(nodeX)}" cy="${fmt(nodeY)}" r="7"
        fill="${CELESTIAL.ivory}"/>` : ""}`;
}

function celestialSystemsLockupMarkup({ x, y, width, height, id }) {
  const glyphSize = height * 0.72;
  const glyphScale = glyphSize / 24;
  const glyphX = x + width * 0.045;
  const glyphY = y + (height - glyphSize) * 0.5;
  const wordX = x + width * 0.28;
  const wordRight = x + width * 0.95;
  const wordWidth = wordRight - wordX;
  const fontScale = Math.min(wordWidth / CINZEL_SYSTEMS_WIDTH, height * 0.27 / 732);
  const baselineY = y + height * 0.59;
  const ruleY = y + height * 0.76;
  return `
    <defs>
      <linearGradient id="${id}-clear" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="${CELESTIAL.obsidian}" stop-opacity="0"/>
        <stop offset="0.1" stop-color="${CELESTIAL.obsidian}" stop-opacity="0.96"/>
        <stop offset="0.9" stop-color="${CELESTIAL.obsidian}" stop-opacity="0.96"/>
        <stop offset="1" stop-color="${CELESTIAL.obsidian}" stop-opacity="0"/>
      </linearGradient>
      <linearGradient id="${id}-ivory" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#FFFFFF"/>
        <stop offset="0.5" stop-color="${CELESTIAL.ivory}"/>
        <stop offset="1" stop-color="#C9C3B8"/>
      </linearGradient>
      <linearGradient id="${id}-rule" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="${CELESTIAL.cyan}" stop-opacity="0.12"/>
        <stop offset="0.22" stop-color="${CELESTIAL.cyan}" stop-opacity="0.85"/>
        <stop offset="0.58" stop-color="${CELESTIAL.ivory}" stop-opacity="0.72"/>
        <stop offset="1" stop-color="${CELESTIAL.ivory}" stop-opacity="0"/>
      </linearGradient>
    </defs>
    <rect x="${fmt(x)}" y="${fmt(y)}" width="${fmt(width)}" height="${fmt(height)}"
      fill="url(#${id}-clear)"/>
    <g transform="translate(${fmt(glyphX)} ${fmt(glyphY)})
      scale(${fmt(glyphScale)} ${fmt(glyphScale)})">
      <path d="${CLEAN_GLYPH_PATH}" fill="none" stroke="url(#${id}-ivory)"
        stroke-width="3" stroke-linecap="square" stroke-linejoin="miter"/>
    </g>
    <path d="${CINZEL_SYSTEMS_PATH}"
      transform="translate(${fmt(wordX)} ${fmt(baselineY)})
        scale(${fmt(fontScale)} ${fmt(-fontScale)})"
      fill="url(#${id}-ivory)"/>
    <path d="M${fmt(wordX)} ${fmt(ruleY)} H${fmt(wordRight)}"
      stroke="url(#${id}-rule)" stroke-width="3"/>
    <path d="M${fmt(wordX)} ${fmt(ruleY - 11)} V${fmt(ruleY + 11)}"
      stroke="${CELESTIAL.cyan}" stroke-opacity="0.75" stroke-width="3"/>`;
}

function celestialSystemsLockupSvg() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="420"
    viewBox="0 0 1800 420">
    ${celestialSystemsLockupMarkup({
      x: 20, y: 20, width: 1760, height: 380, id: "l2-systems-celestial-v7",
    })}
  </svg>`;
}

function wrapV7Svg(stage, bodyLength) {
  const width = 2048;
  const height = 2048;
  const physicalCircumference = Math.PI * 0.164;
  const globalStartM = stage === "sustainer" ? 0 : 0.7;
  const terrain = celestialTerrainPaths(width, height, globalStartM, bodyLength);
  const datum = celestialDatumMarkup({
    width,
    height,
    globalStartM,
    stageLengthM: bodyLength,
    id: `${stage}-celestial-v7`,
    showNode: stage === "sustainer",
  });
  if (stage !== "sustainer") {
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"
      viewBox="0 0 ${width} ${height}">${terrain}${datum}</svg>`;
  }
  const targetAxialLength = 0.365;
  const targetProjectedHeight = 0.082;
  const markWidth = width * targetAxialLength / bodyLength;
  const markHeight = height * targetProjectedHeight / physicalCircumference;
  const markX = width * 0.43 - markWidth * 0.5;
  const markY = height * 0.35 - markHeight * 0.5;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"
    viewBox="0 0 ${width} ${height}">
    ${terrain}
    ${datum}
    ${celestialSystemsLockupMarkup({
      x: markX, y: markY, width: markWidth, height: markHeight,
      id: "sustainer-celestial-systems-v7",
    })}
  </svg>`;
}

function previewV7Svg() {
  const x0 = 275;
  const joint = 840;
  const end = 1648;
  const y0 = 180;
  const bodyH = 160;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="520"
    viewBox="0 0 1800 520">
    <defs>
      <linearGradient id="bg-v7" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#05070B"/>
        <stop offset="1" stop-color="#11151D"/>
      </linearGradient>
      <clipPath id="body-v7">
        <path d="M${x0} ${y0} H1620 Q1660 ${y0} 1660 220
          V300 Q1660 340 1620 340 H${x0} Z"/>
      </clipPath>
    </defs>
    <rect width="1800" height="520" fill="url(#bg-v7)"/>
    <text x="88" y="74" fill="${CELESTIAL.ivory}" font-family="Inter, Arial"
      font-size="24" font-weight="600" letter-spacing="7">L2 // SYSTEMS</text>
    <text x="88" y="106" fill="#9BA0AD" fill-opacity="0.52"
      font-family="JetBrains Mono, monospace" font-size="13" letter-spacing="4">
      OSIFOG // CANDIDATE K // CELESTIAL DATUM V7</text>
    <path d="M${x0} ${y0} H1620 Q1660 ${y0} 1660 220
      V300 Q1660 340 1620 340 H${x0} Z"
      fill="${CELESTIAL.obsidian}" stroke="${CELESTIAL.ivory}" stroke-opacity="0.1"/>
    <path d="M${x0} ${y0} C215 180 150 215 98 260
      C150 305 215 340 ${x0} 340 Z" fill="${CELESTIAL.nose}"/>
    <g clip-path="url(#body-v7)">
      <g transform="translate(${x0} ${y0})
        scale(${fmt((joint - x0) / 2048)} ${fmt(bodyH / 2048)})">
        ${celestialTerrainPaths(2048, 2048, 0, 0.7)}
        ${celestialDatumMarkup({
          width: 2048, height: 2048, globalStartM: 0, stageLengthM: 0.7,
          id: "preview-sustainer-celestial-v7", showNode: true,
        })}
      </g>
      <g transform="translate(${joint} ${y0})
        scale(${fmt((end - joint) / 2048)} ${fmt(bodyH / 2048)})">
        ${celestialTerrainPaths(2048, 2048, 0.7, 1.0)}
        ${celestialDatumMarkup({
          width: 2048, height: 2048, globalStartM: 0.7, stageLengthM: 1.0,
          id: "preview-booster-celestial-v7",
        })}
      </g>
    </g>
    <g transform="translate(372 202) scale(0.23)">
      ${celestialSystemsLockupMarkup({
        x: 0, y: 0, width: 1800, height: 380, id: "preview-lockup-v7",
      })}
    </g>
    <g fill="${CELESTIAL.aftFin}">
      <path d="M1488 180 L1530 96 L1576 180 Z"/>
      <path d="M1488 340 L1530 424 L1576 340 Z"/>
      <path d="M1540 222 L1672 186 L1575 242 Z"/>
    </g>
    <g fill="${CELESTIAL.forwardFin}">
      <path d="M810 180 L850 122 L884 180 Z"/>
      <path d="M810 340 L850 398 L884 340 Z"/>
      <path d="M834 242 L918 222 L880 258 Z"/>
    </g>
    <text x="88" y="468" fill="#A7ACB7" fill-opacity="0.56"
      font-family="JetBrains Mono" font-size="12" letter-spacing="4">
      SATIN_OBSIDIAN // CONTINUOUS_DATUM // SINGLE_INSTITUTIONAL_MARK</text>
  </svg>`;
}

function previewV6Svg() {
  const x0 = 275;
  const joint = 840;
  const end = 1648;
  const y0 = 180;
  const bodyH = 160;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="520"
    viewBox="0 0 1800 520">
    <defs>
      <linearGradient id="bg-v6" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#07080C"/>
        <stop offset="1" stop-color="#0B0D12"/>
      </linearGradient>
      <clipPath id="body-v6">
        <path d="M${x0} ${y0} H1620 Q1660 ${y0} 1660 220
          V300 Q1660 340 1620 340 H${x0} Z"/>
      </clipPath>
    </defs>
    <rect width="1800" height="520" fill="url(#bg-v6)"/>
    <text x="88" y="74" fill="#EDEAE0" font-family="Inter, Arial"
      font-size="24" font-weight="600" letter-spacing="7">L2 // SYSTEMS</text>
    <text x="88" y="106" fill="#9BA0AD" fill-opacity="0.48"
      font-family="JetBrains Mono, monospace" font-size="13" letter-spacing="4">
      OSIFOG // CANDIDATE K // INSTITUTIONAL LIVERY V6</text>
    <path d="M${x0} ${y0} H1620 Q1660 ${y0} 1660 220
      V300 Q1660 340 1620 340 H${x0} Z"
      fill="#0B0D12" stroke="#EDEAE0" stroke-opacity="0.12"/>
    <path d="M${x0} ${y0} C215 180 150 215 98 260
      C150 305 215 340 ${x0} 340 Z" fill="#07080C"/>
    <g clip-path="url(#body-v6)">
      <g transform="translate(${x0} ${y0})">
        <g transform="scale(${fmt((joint - x0) / 2048)} ${fmt(bodyH / 2048)})">
          ${topographicFieldPaths(2048, 2048, 0, 0.7)}
        </g>
      </g>
      <g transform="translate(${joint} ${y0})">
        <g transform="scale(${fmt((end - joint) / 2048)} ${fmt(bodyH / 2048)})">
          ${topographicFieldPaths(2048, 2048, 0.7, 1.0)}
        </g>
      </g>
    </g>
    <g transform="translate(382 219) scale(0.235)">
      ${atlasGradeSystemsLockupMarkup({
        x: 0, y: 0, width: 1800, height: 360, id: "preview-atlas-grade-v6",
      })}
    </g>
    <g fill="${COLORS.violet}">
      <path d="M1488 180 L1530 96 L1576 180 Z"/>
      <path d="M1488 340 L1530 424 L1576 340 Z"/>
      <path d="M1540 222 L1672 186 L1575 242 Z"/>
    </g>
    <g fill="${COLORS.cyan}">
      <path d="M810 180 L850 122 L884 180 Z"/>
      <path d="M810 340 L850 398 L884 340 Z"/>
      <path d="M834 242 L918 222 L880 258 Z"/>
    </g>
    <text x="88" y="468" fill="#9BA0AD" fill-opacity="0.5"
      font-family="JetBrains Mono" font-size="12" letter-spacing="4">
      CINZEL_600_OUTLINES // SINGLE_PRIMARY_MARK // CONTROLLED_SIGNAL</text>
  </svg>`;
}

function previewV5Svg() {
  const x0 = 275;
  const joint = 840;
  const end = 1648;
  const y0 = 180;
  const bodyH = 160;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="520"
    viewBox="0 0 1800 520">
    <defs>
      <linearGradient id="bg-v5" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#020202"/>
        <stop offset="1" stop-color="#090817"/>
      </linearGradient>
      <clipPath id="body-v5">
        <path d="M${x0} ${y0} H1620 Q1660 ${y0} 1660 220
          V300 Q1660 340 1620 340 H${x0} Z"/>
      </clipPath>
    </defs>
    <rect width="1800" height="520" fill="url(#bg-v5)"/>
    <text x="88" y="74" fill="${COLORS.titanium}" font-family="Inter, Arial"
      font-size="26" font-weight="700" letter-spacing="8">L2 // SYSTEMS</text>
    <text x="88" y="106" fill="${COLORS.titanium}" fill-opacity="0.42"
      font-family="JetBrains Mono, monospace" font-size="14" letter-spacing="4">
      OSIFOG // CANDIDATE K // CLEAN GLYPH V5</text>
    <path d="M${x0} ${y0} H1620 Q1660 ${y0} 1660 220
      V300 Q1660 340 1620 340 H${x0} Z"
      fill="${COLORS.void}" stroke="${COLORS.titanium}" stroke-opacity="0.16"/>
    <path d="M${x0} ${y0} C215 180 150 215 98 260
      C150 305 215 340 ${x0} 340 Z" fill="${COLORS.deep}"/>
    <g clip-path="url(#body-v5)">
      <g transform="translate(${x0} ${y0})">
        <g transform="scale(${fmt((joint - x0) / 2048)} ${fmt(bodyH / 2048)})">
          ${topographicFieldPaths(2048, 2048, 0, 0.7)}
        </g>
      </g>
      <g transform="translate(${joint} ${y0})">
        <g transform="scale(${fmt((end - joint) / 2048)} ${fmt(bodyH / 2048)})">
          ${topographicFieldPaths(2048, 2048, 0.7, 1.0)}
        </g>
      </g>
    </g>
    <rect x="${joint - 2}" y="${y0}" width="4" height="${bodyH}"
      fill="${COLORS.titanium}" fill-opacity="0.1"/>
    <g transform="translate(414 211) scale(0.19)">
      ${cleanSystemsLockupMarkup({
        x: 0, y: 0, width: 1600, height: 500, id: "preview-clean-v5",
      })}
    </g>
    <g fill="${COLORS.violet}">
      <path d="M1488 180 L1530 96 L1576 180 Z"/>
      <path d="M1488 340 L1530 424 L1576 340 Z"/>
      <path d="M1540 222 L1672 186 L1575 242 Z"/>
    </g>
    <g fill="${COLORS.cyan}">
      <path d="M810 180 L850 122 L884 180 Z"/>
      <path d="M810 340 L850 398 L884 340 Z"/>
      <path d="M834 242 L918 222 L880 258 Z"/>
    </g>
    <text x="88" y="468" fill="${COLORS.titanium}" fill-opacity="0.44"
      font-family="JetBrains Mono" font-size="13" letter-spacing="4">
      CLEAN_GLYPH // VECTOR_WORDMARK // NO_FONT_SUBSTITUTION</text>
  </svg>`;
}

function previewV4Svg() {
  const x0 = 275;
  const joint = 840;
  const end = 1648;
  const y0 = 180;
  const bodyH = 160;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="520"
    viewBox="0 0 1800 520">
    <defs>
      <linearGradient id="bg-v4" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#020202"/>
        <stop offset="1" stop-color="#090817"/>
      </linearGradient>
      <clipPath id="body-v4">
        <path d="M${x0} ${y0} H1620 Q1660 ${y0} 1660 220
          V300 Q1660 340 1620 340 H${x0} Z"/>
      </clipPath>
    </defs>
    <rect width="1800" height="520" fill="url(#bg-v4)"/>
    <text x="88" y="74" fill="${COLORS.titanium}" font-family="Inter, Arial"
      font-size="26" font-weight="700" letter-spacing="8">L2 // SYSTEMS</text>
    <text x="88" y="106" fill="${COLORS.titanium}" fill-opacity="0.42"
      font-family="JetBrains Mono, monospace" font-size="14" letter-spacing="4">
      OSIFOG // CANDIDATE K // LIVING TERRAIN V4</text>
    <path d="M${x0} ${y0} H1620 Q1660 ${y0} 1660 220
      V300 Q1660 340 1620 340 H${x0} Z"
      fill="${COLORS.void}" stroke="${COLORS.titanium}" stroke-opacity="0.16"/>
    <path d="M${x0} ${y0} C215 180 150 215 98 260
      C150 305 215 340 ${x0} 340 Z" fill="${COLORS.deep}"/>
    <g clip-path="url(#body-v4)">
      <g transform="translate(${x0} ${y0})">
        <g transform="scale(${fmt((joint - x0) / 2048)} ${fmt(bodyH / 2048)})">
          ${topographicFieldPaths(2048, 2048, 0, 0.7)}
        </g>
      </g>
      <g transform="translate(${joint} ${y0})">
        <g transform="scale(${fmt((end - joint) / 2048)} ${fmt(bodyH / 2048)})">
          ${topographicFieldPaths(2048, 2048, 0.7, 1.0)}
        </g>
      </g>
    </g>
    <rect x="${joint - 2}" y="${y0}" width="4" height="${bodyH}"
      fill="${COLORS.titanium}" fill-opacity="0.1"/>
    <g transform="translate(420 211) scale(0.19)">
      ${terrainSystemsLockupMarkup({
        x: 0, y: 0, width: 1600, height: 500, id: "preview-terrain-v4",
      })}
    </g>
    <g fill="${COLORS.violet}">
      <path d="M1488 180 L1530 96 L1576 180 Z"/>
      <path d="M1488 340 L1530 424 L1576 340 Z"/>
      <path d="M1540 222 L1672 186 L1575 242 Z"/>
    </g>
    <g fill="${COLORS.cyan}">
      <path d="M810 180 L850 122 L884 180 Z"/>
      <path d="M810 340 L850 398 L884 340 Z"/>
      <path d="M834 242 L918 222 L880 258 Z"/>
    </g>
    <text x="88" y="468" fill="${COLORS.titanium}" fill-opacity="0.44"
      font-family="JetBrains Mono" font-size="13" letter-spacing="4">
      HEIGHTFIELD_CONTOURS // CONTINUOUS_SEAM // SIGNAL_LEVELS_ONLY</text>
  </svg>`;
}

function previewV3Svg() {
  const sustainerX = 275;
  const jointX = 840;
  const endX = 1648;
  const bodyY = 180;
  const bodyH = 160;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="520"
    viewBox="0 0 1800 520">
    <defs>
      <linearGradient id="bg-v3" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#020202"/>
        <stop offset="1" stop-color="#090817"/>
      </linearGradient>
      <clipPath id="body-v3">
        <path d="M${sustainerX} ${bodyY} H1620 Q1660 ${bodyY} 1660 220
          V300 Q1660 340 1620 340 H${sustainerX} Z"/>
      </clipPath>
    </defs>
    <rect width="1800" height="520" fill="url(#bg-v3)"/>
    <text x="88" y="74" fill="${COLORS.titanium}" font-family="Inter, Arial"
      font-size="26" font-weight="700" letter-spacing="8">L2 // SYSTEMS</text>
    <text x="88" y="106" fill="${COLORS.titanium}" fill-opacity="0.42"
      font-family="JetBrains Mono, monospace" font-size="14" letter-spacing="4">
      OSIFOG // CANDIDATE K // CONTINUOUS TERRAIN V3</text>
    <path d="M${sustainerX} ${bodyY} H1620 Q1660 ${bodyY} 1660 220
      V300 Q1660 340 1620 340 H${sustainerX} Z"
      fill="${COLORS.void}" stroke="${COLORS.titanium}" stroke-opacity="0.16"/>
    <path d="M${sustainerX} ${bodyY} C215 180 150 215 98 260
      C150 305 215 340 ${sustainerX} 340 Z" fill="${COLORS.deep}"/>
    <g clip-path="url(#body-v3)">
      <g transform="translate(${sustainerX} ${bodyY})">
        <g transform="scale(${fmt((jointX - sustainerX) / 2048)} ${fmt(bodyH / 2048)})">
          ${continuousContourPaths(2048, 2048, 0, 0.7)}
        </g>
      </g>
      <g transform="translate(${jointX} ${bodyY})">
        <g transform="scale(${fmt((endX - jointX) / 2048)} ${fmt(bodyH / 2048)})">
          ${continuousContourPaths(2048, 2048, 0.7, 1.0)}
        </g>
      </g>
    </g>
    <rect x="${jointX - 2}" y="${bodyY}" width="4" height="${bodyH}"
      fill="${COLORS.titanium}" fill-opacity="0.14"/>
    <g transform="translate(430 213) scale(0.18)">
      ${systemsLockupMarkup({
        x: 0,
        y: 0,
        width: 1600,
        height: 500,
        id: "preview-v3",
      })}
    </g>
    <g fill="${COLORS.violet}">
      <path d="M1488 180 L1530 96 L1576 180 Z"/>
      <path d="M1488 340 L1530 424 L1576 340 Z"/>
      <path d="M1540 222 L1672 186 L1575 242 Z"/>
    </g>
    <g fill="${COLORS.cyan}">
      <path d="M810 180 L850 122 L884 180 Z"/>
      <path d="M810 340 L850 398 L884 340 Z"/>
      <path d="M834 242 L918 222 L880 258 Z"/>
    </g>
    <text x="88" y="468" fill="${COLORS.titanium}" fill-opacity="0.44"
      font-family="JetBrains Mono" font-size="13" letter-spacing="4">
      GLOBAL_FIELD_1.70M // SEAM_CONTINUITY_LOCKED // L2_SYSTEMS_LOCKUP</text>
  </svg>`;
}

function previewV2Svg() {
  const bodyStart = 275;
  const bodyEnd = 1660;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="520"
    viewBox="0 0 1800 520">
    <defs>
      <linearGradient id="bg-v2" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#020202"/>
        <stop offset="1" stop-color="#090817"/>
      </linearGradient>
      <clipPath id="body-v2">
        <path d="M${bodyStart} 180 H1620 Q${bodyEnd} 180 ${bodyEnd} 220
          V300 Q${bodyEnd} 340 1620 340 H${bodyStart} Z"/>
      </clipPath>
    </defs>
    <rect width="1800" height="520" fill="url(#bg-v2)"/>
    <text x="88" y="74" fill="${COLORS.titanium}" font-family="Inter, Arial"
      font-size="26" font-weight="700" letter-spacing="8">L2 // SYSTEMS</text>
    <text x="88" y="106" fill="${COLORS.titanium}" fill-opacity="0.42"
      font-family="JetBrains Mono, monospace" font-size="14" letter-spacing="4">
      OSIFOG // CANDIDATE K // TOPOGRAPHIC RAIL MARK V2</text>
    <path d="M${bodyStart} 180 H1620 Q${bodyEnd} 180 ${bodyEnd} 220
      V300 Q${bodyEnd} 340 1620 340 H${bodyStart} Z"
      fill="${COLORS.void}" stroke="${COLORS.titanium}" stroke-opacity="0.16"/>
    <path d="M${bodyStart} 180 C215 180 150 215 98 260
      C150 305 215 340 ${bodyStart} 340 Z" fill="${COLORS.deep}"/>
    <g clip-path="url(#body-v2)">
      <g transform="translate(275 180)">
        <g transform="scale(0.676 0.078)">
          ${rocketContourPaths(2048, 2048, "booster")}
        </g>
      </g>
    </g>
    <rect x="450" y="201" width="142" height="118" rx="12"
      fill="${COLORS.deep}" fill-opacity="0.96"
      stroke="${COLORS.titanium}" stroke-opacity="0.18"/>
    <g transform="translate(478 211) scale(4.05 4.05)">
      <path d="${GLYPH_PATH}" fill="none" stroke="#020202" stroke-width="4.4"
        stroke-linecap="square" stroke-linejoin="miter"/>
      <path d="${GLYPH_PATH}" fill="none" stroke="${COLORS.titanium}" stroke-width="3"
        stroke-linecap="square" stroke-linejoin="miter"/>
      <rect x="11" y="17" width="2" height="2" fill="${COLORS.titanium}"/>
    </g>
    <path d="M466 211 V309" stroke="${COLORS.cyan}" stroke-width="6"/>
    <path d="M576 211 V309" stroke="${COLORS.violet}" stroke-width="6"/>
    <g fill="${COLORS.violet}">
      <path d="M1488 180 L1530 96 L1576 180 Z"/>
      <path d="M1488 340 L1530 424 L1576 340 Z"/>
      <path d="M1540 222 L1672 186 L1575 242 Z"/>
    </g>
    <g fill="${COLORS.cyan}">
      <path d="M834 180 L872 122 L904 180 Z"/>
      <path d="M834 340 L872 398 L904 340 Z"/>
      <path d="M856 242 L936 222 L902 258 Z"/>
    </g>
    <text x="88" y="468" fill="${COLORS.titanium}" fill-opacity="0.44"
      font-family="JetBrains Mono" font-size="13" letter-spacing="4">
      THICK_CONTOURS // COMPACT_GLYPH // OPENROCKET_MIP_SAFE</text>
  </svg>`;
}

function previewSvg(mode) {
  const topographic = mode === "topographic";
  const contours = topographic ? contourPaths(1360, 160, 13, 10) : "";
  const bodyStart = 275;
  const sustainerEnd = 865;
  const bodyEnd = 1660;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="520"
    viewBox="0 0 1800 520">
    <defs>
      <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#020202"/>
        <stop offset="1" stop-color="#090817"/>
      </linearGradient>
      <linearGradient id="signal" x1="0" y1="1" x2="1" y2="0">
        <stop offset="0" stop-color="${COLORS.cyan}"/>
        <stop offset="1" stop-color="${COLORS.violet}"/>
      </linearGradient>
      <clipPath id="body-clip">
        <path d="M${bodyStart} 180 H1620 Q${bodyEnd} 180 ${bodyEnd} 220
          V300 Q${bodyEnd} 340 1620 340 H${bodyStart} Z"/>
      </clipPath>
      <filter id="glow"><feGaussianBlur stdDeviation="9"/></filter>
    </defs>
    <rect width="1800" height="520" fill="url(#bg)"/>
    <g opacity="0.16">${contourPaths(1800, 520, 22, 24)}</g>
    <text x="88" y="74" fill="${COLORS.titanium}" font-family="Inter, Arial"
      font-size="26" font-weight="700" letter-spacing="8">L2 // SYSTEMS</text>
    <text x="88" y="106" fill="${COLORS.titanium}" fill-opacity="0.42"
      font-family="JetBrains Mono, monospace" font-size="14" letter-spacing="4">
      OSIFOG // CANDIDATE K // ${mode.toUpperCase()}</text>
    <path d="M${bodyStart} 180 H1620 Q${bodyEnd} 180 ${bodyEnd} 220
      V300 Q${bodyEnd} 340 1620 340 H${bodyStart} Z"
      fill="${topographic ? COLORS.void : COLORS.surface}"
      stroke="${COLORS.titanium}" stroke-opacity="0.16"/>
    <path d="M${bodyStart} 180 C215 180 150 215 98 260
      C150 305 215 340 ${bodyStart} 340 Z"
      fill="${topographic ? COLORS.deep : COLORS.violet}"/>
    <g clip-path="url(#body-clip)">
      <g transform="translate(${bodyStart} 180)">
        ${contours}
        ${contours}
      </g>
      <rect x="${sustainerEnd - 12}" y="180" width="24" height="160"
        fill="${COLORS.titanium}" fill-opacity="0.18"/>
    </g>
    <g transform="translate(470 199) scale(5.1 5.1)">
      <path d="${GLYPH_PATH}" fill="none" stroke="${COLORS.titanium}"
        stroke-width="3" stroke-linecap="square" stroke-linejoin="miter"/>
      <rect x="11" y="17" width="2" height="2" fill="${COLORS.titanium}"/>
    </g>
    <text x="620" y="252" fill="${COLORS.titanium}" font-family="JetBrains Mono"
      font-size="24" font-weight="700" letter-spacing="6">L2 // SYSTEMS</text>
    <text x="620" y="282" fill="${topographic ? COLORS.cyan : COLORS.titanium}"
      fill-opacity="0.7" font-family="JetBrains Mono" font-size="13"
      letter-spacing="4">SUSTAINER // STAGE 00</text>
    <text x="1110" y="252" fill="${COLORS.titanium}" font-family="JetBrains Mono"
      font-size="19" font-weight="700" letter-spacing="5">OSIFOG // L3</text>
    <text x="1110" y="280" fill="${topographic ? COLORS.violet : COLORS.cyan}"
      fill-opacity="0.75" font-family="JetBrains Mono" font-size="12"
      letter-spacing="4">BOOSTER // STAGE 01</text>
    <g fill="${COLORS.violet}">
      <path d="M1488 180 L1530 96 L1576 180 Z"/>
      <path d="M1488 340 L1530 424 L1576 340 Z"/>
      <path d="M1540 222 L1672 186 L1575 242 Z"/>
    </g>
    <g fill="${COLORS.cyan}">
      <path d="M834 180 L872 122 L904 180 Z"/>
      <path d="M834 340 L872 398 L904 340 Z"/>
      <path d="M856 242 L936 222 L902 258 Z"/>
    </g>
    <path d="M95 260 H1665" stroke="url(#signal)" stroke-width="5"
      stroke-opacity="0.45" filter="url(#glow)"/>
    <path d="M95 260 H1665" stroke="url(#signal)" stroke-width="2"
      stroke-opacity="0.85"/>
    <text x="88" y="468" fill="${COLORS.titanium}" fill-opacity="0.44"
      font-family="JetBrains Mono" font-size="13" letter-spacing="4">
      APPEARANCE_ONLY // FINISH_NORMAL_LOCKED // TELEMETRY_IDENTITY_REQUIRED</text>
  </svg>`;
}

async function writeSvgAndPng(name, svg, width = null, height = null) {
  const svgPath = path.join(GENERATED, `${name}.svg`);
  const pngPath = path.join(GENERATED, `${name}.png`);
  fs.writeFileSync(svgPath, svg, "utf8");
  let image = sharp(Buffer.from(svg));
  if (width && height) {
    image = image.resize(width, height);
  }
  await image.png({ compressionLevel: 9, palette: false }).toFile(pngPath);
}

function decal(name, edgeMode = "REPEAT") {
  return {
    name: `decals/${name}.png`,
    // OpenRocket body-tube UVs use X around circumference and Y axially.
    // Rotate the square texture 90° so the glyph and labels read along the
    // rocket's longitudinal axis in side view.
    rotation: Math.PI / 2,
    edgemode: edgeMode,
    center: { x: 0.0, y: 0.0 },
    offset: { x: 0.0, y: 0.0 },
    scale: { x: 1.0, y: 1.0 },
  };
}

function baseComponents(sustainerDecal, boosterDecal) {
  return {
    "Nose Cone": { paint: COLORS.deep, shine: 0.38 },
    "Sustainer Airframe": {
      paint: COLORS.void,
      shine: 0.28,
      decal: sustainerDecal,
    },
    "Booster Airframe": {
      paint: COLORS.deep,
      shine: 0.24,
      decal: boosterDecal,
    },
    "Booster Fins": { paint: COLORS.violet, shine: 0.32 },
    "Booster Forward Grid Fins": { paint: COLORS.cyan, shine: 0.32 },
    "Booster-Retained Interstage Coupler": {
      paint: "#111111",
      shine: 0.12,
    },
  };
}

function celestialBaseComponents(sustainerDecal, boosterDecal) {
  return {
    "Nose Cone": { paint: CELESTIAL.nose, shine: 0.34 },
    "Sustainer Airframe": {
      paint: CELESTIAL.obsidian,
      shine: 0.24,
      decal: sustainerDecal,
    },
    "Booster Airframe": {
      paint: CELESTIAL.obsidian,
      shine: 0.22,
      decal: boosterDecal,
    },
    "Booster Fins": { paint: CELESTIAL.aftFin, shine: 0.27 },
    "Booster Forward Grid Fins": {
      paint: CELESTIAL.forwardFin,
      shine: 0.27,
    },
    "Booster-Retained Interstage Coupler": {
      paint: CELESTIAL.coupler,
      shine: 0.14,
    },
  };
}

async function main() {
  await writeSvgAndPng("l2-glyph-topographic-dark", enhancedGlyphSvg("dark"));
  await writeSvgAndPng("l2-glyph-topographic-light", enhancedGlyphSvg("light"));
  await writeSvgAndPng(
    "l2-topographic-sustainer",
    wrapSvg("sustainer", 0.7, false)
  );
  await writeSvgAndPng(
    "l2-topographic-booster",
    wrapSvg("booster", 1.0, false)
  );
  await writeSvgAndPng(
    "l2-signal-sustainer",
    wrapSvg("sustainer", 0.7, true)
  );
  await writeSvgAndPng(
    "l2-signal-booster",
    wrapSvg("booster", 1.0, true)
  );
  await writeSvgAndPng(
    "candidate_K_topographic_preview",
    previewSvg("topographic"),
    1800,
    520
  );
  await writeSvgAndPng(
    "candidate_K_signal_preview",
    previewSvg("signal"),
    1800,
    520
  );
  await writeSvgAndPng(
    "l2-topographic-rail-v2-sustainer",
    wrapV2Svg("sustainer", 0.7)
  );
  await writeSvgAndPng(
    "l2-topographic-rail-v2-booster",
    wrapV2Svg("booster", 1.0)
  );
  await writeSvgAndPng(
    "candidate_K_topographic_rail_v2_preview",
    previewV2Svg(),
    1800,
    520
  );
  await writeSvgAndPng("l2-systems-rail-lockup-v3", systemsLockupSvg());
  await writeSvgAndPng(
    "l2-topographic-continuous-v3-sustainer",
    wrapV3Svg("sustainer", 0.7)
  );
  await writeSvgAndPng(
    "l2-topographic-continuous-v3-booster",
    wrapV3Svg("booster", 1.0)
  );
  await writeSvgAndPng(
    "candidate_K_topographic_continuous_v3_preview",
    previewV3Svg(),
    1800,
    520
  );
  await writeSvgAndPng(
    "l2-systems-terrain-lockup-v4",
    terrainSystemsLockupSvg()
  );
  await writeSvgAndPng(
    "l2-living-terrain-v4-sustainer",
    wrapV4Svg("sustainer", 0.7)
  );
  await writeSvgAndPng(
    "l2-living-terrain-v4-booster",
    wrapV4Svg("booster", 1.0)
  );
  await writeSvgAndPng(
    "candidate_K_living_terrain_v4_preview",
    previewV4Svg(),
    1800,
    520
  );
  await writeSvgAndPng(
    "l2-systems-clean-lockup-v5",
    cleanSystemsLockupSvg()
  );
  await writeSvgAndPng(
    "l2-living-terrain-clean-v5-sustainer",
    wrapV5Svg("sustainer", 0.7)
  );
  await writeSvgAndPng(
    "l2-living-terrain-clean-v5-booster",
    wrapV5Svg("booster", 1.0)
  );
  await writeSvgAndPng(
    "candidate_K_living_terrain_clean_v5_preview",
    previewV5Svg(),
    1800,
    520
  );
  await writeSvgAndPng(
    "l2-systems-institutional-lockup-v6",
    atlasGradeSystemsLockupSvg()
  );
  await writeSvgAndPng(
    "l2-living-terrain-institutional-v6-sustainer",
    wrapV6Svg("sustainer", 0.7)
  );
  await writeSvgAndPng(
    "l2-living-terrain-institutional-v6-booster",
    wrapV6Svg("booster", 1.0)
  );
  await writeSvgAndPng(
    "candidate_K_living_terrain_institutional_v6_preview",
    previewV6Svg(),
    1800,
    520
  );

  const candidateK = JSON.parse(
    fs.readFileSync(
      path.join(REPO, "designs", "osifog_submission", "candidate_K.json"),
      "utf8"
    )
  );
  const topographicAssets = [
    "l2-topographic-sustainer",
    "l2-topographic-booster",
  ];
  const signalAssets = ["l2-signal-sustainer", "l2-signal-booster"];
  const railV2Assets = [
    "l2-topographic-rail-v2-sustainer",
    "l2-topographic-rail-v2-booster",
  ];
  const continuousV3Assets = [
    "l2-topographic-continuous-v3-sustainer",
    "l2-topographic-continuous-v3-booster",
  ];
  const livingV4Assets = [
    "l2-living-terrain-v4-sustainer",
    "l2-living-terrain-v4-booster",
  ];
  const cleanV5Assets = [
    "l2-living-terrain-clean-v5-sustainer",
    "l2-living-terrain-clean-v5-booster",
  ];
  const institutionalV6Assets = [
    "l2-living-terrain-institutional-v6-sustainer",
    "l2-living-terrain-institutional-v6-booster",
  ];

  const topographic = {
    ...candidateK,
    livery: {
      name: "L2 Black Box Topographic",
      components: baseComponents(
        decal(topographicAssets[0]),
        decal(topographicAssets[1])
      ),
    },
    livery_decals: topographicAssets.map((name) => ({
      path: `designs/osifog_visuals/assets/generated/${name}.png`,
      zip_name: `decals/${name}.png`,
    })),
  };
  const signal = {
    ...candidateK,
    livery: {
      name: "L2 Signal Recolor",
      components: baseComponents(
        decal(signalAssets[0], "CLAMP"),
        decal(signalAssets[1], "CLAMP")
      ),
    },
    livery_decals: signalAssets.map((name) => ({
      path: `designs/osifog_visuals/assets/generated/${name}.png`,
      zip_name: `decals/${name}.png`,
    })),
  };
  const railV2 = {
    ...candidateK,
    livery: {
      name: "L2 Topographic Rail Mark V2",
      components: baseComponents(
        decal(railV2Assets[0]),
        decal(railV2Assets[1])
      ),
    },
    livery_decals: railV2Assets.map((name) => ({
      path: `designs/osifog_visuals/assets/generated/${name}.png`,
      zip_name: `decals/${name}.png`,
    })),
  };
  const continuousV3 = {
    ...candidateK,
    livery: {
      name: "L2 Continuous Terrain V3",
      components: baseComponents(
        decal(continuousV3Assets[0]),
        decal(continuousV3Assets[1])
      ),
    },
    livery_decals: continuousV3Assets.map((name) => ({
      path: `designs/osifog_visuals/assets/generated/${name}.png`,
      zip_name: `decals/${name}.png`,
    })),
  };
  const livingV4 = {
    ...candidateK,
    livery: {
      name: "L2 Living Terrain V4",
      components: baseComponents(
        decal(livingV4Assets[0]),
        decal(livingV4Assets[1])
      ),
    },
    livery_decals: livingV4Assets.map((name) => ({
      path: `designs/osifog_visuals/assets/generated/${name}.png`,
      zip_name: `decals/${name}.png`,
    })),
  };
  const cleanV5 = {
    ...candidateK,
    livery: {
      name: "L2 Living Terrain Clean V5",
      components: baseComponents(
        decal(cleanV5Assets[0]),
        decal(cleanV5Assets[1])
      ),
    },
    livery_decals: cleanV5Assets.map((name) => ({
      path: `designs/osifog_visuals/assets/generated/${name}.png`,
      zip_name: `decals/${name}.png`,
    })),
  };
  const institutionalV6 = {
    ...candidateK,
    livery: {
      name: "L2 Living Terrain Institutional V6",
      components: baseComponents(
        decal(institutionalV6Assets[0]),
        decal(institutionalV6Assets[1])
      ),
    },
    livery_decals: institutionalV6Assets.map((name) => ({
      path: `designs/osifog_visuals/assets/generated/${name}.png`,
      zip_name: `decals/${name}.png`,
    })),
  };

  fs.writeFileSync(
    path.join(ROOT, "candidate_K_topographic.json"),
    `${JSON.stringify(topographic, null, 2)}\n`,
    "utf8"
  );
  fs.writeFileSync(
    path.join(ROOT, "candidate_K_signal_fallback.json"),
    `${JSON.stringify(signal, null, 2)}\n`,
    "utf8"
  );
  fs.writeFileSync(
    path.join(ROOT, "candidate_K_topographic_rail_v2.json"),
    `${JSON.stringify(railV2, null, 2)}\n`,
    "utf8"
  );
  fs.writeFileSync(
    path.join(ROOT, "candidate_K_topographic_continuous_v3.json"),
    `${JSON.stringify(continuousV3, null, 2)}\n`,
    "utf8"
  );
  fs.writeFileSync(
    path.join(ROOT, "candidate_K_living_terrain_v4.json"),
    `${JSON.stringify(livingV4, null, 2)}\n`,
    "utf8"
  );
  fs.writeFileSync(
    path.join(ROOT, "candidate_K_living_terrain_clean_v5.json"),
    `${JSON.stringify(cleanV5, null, 2)}\n`,
    "utf8"
  );
  fs.writeFileSync(
    path.join(ROOT, "candidate_K_living_terrain_institutional_v6.json"),
    `${JSON.stringify(institutionalV6, null, 2)}\n`,
    "utf8"
  );
}

async function activeMain() {
  await writeSvgAndPng(
    "l2-systems-institutional-lockup-v6",
    atlasGradeSystemsLockupSvg()
  );
  await writeSvgAndPng(
    "l2-living-terrain-institutional-v6-sustainer",
    wrapV6Svg("sustainer", 0.7)
  );
  await writeSvgAndPng(
    "l2-living-terrain-institutional-v6-booster",
    wrapV6Svg("booster", 1.0)
  );
  await writeSvgAndPng(
    "candidate_K_living_terrain_institutional_v6_preview",
    previewV6Svg(),
    1800,
    520
  );
  await writeSvgAndPng(
    "l2-systems-celestial-datum-v7",
    celestialSystemsLockupSvg()
  );
  await writeSvgAndPng(
    "l2-celestial-datum-v7-sustainer",
    wrapV7Svg("sustainer", 0.7)
  );
  await writeSvgAndPng(
    "l2-celestial-datum-v7-booster",
    wrapV7Svg("booster", 1.0)
  );
  await writeSvgAndPng(
    "candidate_K_celestial_datum_v7_preview",
    previewV7Svg(),
    1800,
    520
  );

  const candidateK = JSON.parse(
    fs.readFileSync(
      path.join(REPO, "designs", "osifog_submission", "candidate_K.json"),
      "utf8"
    )
  );
  const institutionalV6Assets = [
    "l2-living-terrain-institutional-v6-sustainer",
    "l2-living-terrain-institutional-v6-booster",
  ];
  const celestialV7Assets = [
    "l2-celestial-datum-v7-sustainer",
    "l2-celestial-datum-v7-booster",
  ];
  const institutionalV6 = {
    ...candidateK,
    livery: {
      name: "L2 Living Terrain Institutional V6",
      components: baseComponents(
        decal(institutionalV6Assets[0]),
        decal(institutionalV6Assets[1])
      ),
    },
    livery_decals: institutionalV6Assets.map((name) => ({
      path: `designs/osifog_visuals/assets/generated/${name}.png`,
      zip_name: `decals/${name}.png`,
    })),
  };
  const celestialV7 = {
    ...candidateK,
    livery: {
      name: "L2 Celestial Datum V7",
      components: celestialBaseComponents(
        decal(celestialV7Assets[0]),
        decal(celestialV7Assets[1])
      ),
    },
    livery_decals: celestialV7Assets.map((name) => ({
      path: `designs/osifog_visuals/assets/generated/${name}.png`,
      zip_name: `decals/${name}.png`,
    })),
  };
  fs.writeFileSync(
    path.join(ROOT, "candidate_K_living_terrain_institutional_v6.json"),
    `${JSON.stringify(institutionalV6, null, 2)}\n`,
    "utf8"
  );
  fs.writeFileSync(
    path.join(ROOT, "candidate_K_celestial_datum_v7.json"),
    `${JSON.stringify(celestialV7, null, 2)}\n`,
    "utf8"
  );
}

activeMain().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
