import sys
import math
import jpype
import orhelper

def export_kml(ork_file, output_kml, base_lat=-23.5505, base_lon=-46.6333):
    print(f"[*] Gerando KML de trajetória para {ork_file}")
    
    with orhelper.OpenRocketInstance('lib/OpenRocket-.jar') as instance:
        orh = orhelper.Helper(instance)
        doc = orh.load_doc(ork_file)
        
        # Pega a simulação
        sim = doc.getSimulations().get(0)
        orh.run_simulation(sim)
        data = sim.getSimulatedData().getBranch(0)
        
        FDT = jpype.JClass('net.sf.openrocket.simulation.FlightDataType')
        
        # Pega as listas de posições relativas
        xs = list(data.get(FDT.TYPE_POSITION_X)) # Leste
        ys = list(data.get(FDT.TYPE_POSITION_Y)) # Norte
        alts = list(data.get(FDT.TYPE_ALTITUDE)) # Cima
        
        # Converter X, Y para Lat/Lon a partir de uma base
        # 1 deg latitude = ~111.32 km
        lat_conversion = 1.0 / 111320.0
        lon_conversion = 1.0 / (111320.0 * math.cos(math.radians(base_lat)))
        
        kml_coords = []
        for x, y, alt in zip(xs, ys, alts):
            # ignora voo abaixo do solo na descida
            if alt < 0: alt = 0
            lat = base_lat + (y * lat_conversion)
            lon = base_lon + (x * lon_conversion)
            kml_coords.append(f"{lon},{lat},{alt}")
            
        coords_str = "\n".join(kml_coords)
        
        kml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Voo - L2 Systems</name>
    <description>Simulação L2 Apex</description>
    <Style id="linhaVoo">
      <LineStyle>
        <color>ff0000ff</color>
        <width>4</width>
      </LineStyle>
      <PolyStyle>
        <color>7f0000ff</color>
      </PolyStyle>
    </Style>
    <Placemark>
      <name>Trajetória do Foguete</name>
      <styleUrl>#linhaVoo</styleUrl>
      <LineString>
        <extrude>1</extrude>
        <tessellate>1</tessellate>
        <altitudeMode>relativeToGround</altitudeMode>
        <coordinates>
{coords_str}
        </coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""
        with open(output_kml, 'w', encoding='utf-8') as f:
            f.write(kml_content)
            
        print(f"[!] KML exportado com sucesso: {output_kml}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("ork_file", help="Caminho para o arquivo .ork")
    parser.add_argument("out_kml", help="Caminho de saida .kml")
    args = parser.parse_args()
    
    export_kml(args.ork_file, args.out_kml)
