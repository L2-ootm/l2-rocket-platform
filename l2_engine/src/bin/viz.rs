use eframe::egui;
use egui_plot::{Line, Plot, PlotPoints};

use l2_engine::sim_core::dynamics::state::{SimConfig, State};
use l2_engine::sim_core::physics::atmosphere;
use l2_engine::sim_core::sim;
use l2_engine::sim_core::vehicle::{Mission, presets};

fn main() -> eframe::Result {
    let mission = presets::pathfinder();
    let config = SimConfig {
        dt: 0.005,
        max_time: 600.0,
    };
    let (trajectory, _) = sim::simulate(&mission, &config);

    let app = SimViz {
        trajectory,
        mission,
    };
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default().with_inner_size([1200.0, 800.0]),
        ..Default::default()
    };
    eframe::run_native(
        "Rocket Flight Simulator",
        options,
        Box::new(|_| Ok(Box::new(app))),
    )
}

struct SimViz {
    trajectory: Vec<State>,
    mission: Mission,
}

impl eframe::App for SimViz {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        let step = (self.trajectory.len() / 2000).max(1);
        let sampled: Vec<&State> = self.trajectory.iter().step_by(step).collect();

        egui::TopBottomPanel::top("header").show(ctx, |ui| {
            ui.heading(format!("Mission: {}", self.mission.name));
            let apogee = self
                .trajectory
                .iter()
                .map(|s| s.pos.z)
                .fold(0.0_f64, f64::max);
            let max_v = self
                .trajectory
                .iter()
                .map(|s| s.vel.norm())
                .fold(0.0_f64, f64::max);
            ui.label(format!(
                "Apogee: {:.1} km  |  Max speed: Mach {:.2}  |  Stages: {}  |  Flight: {:.0} s",
                apogee / 1000.0,
                max_v / atmosphere::isa(0.0).sound_speed,
                self.mission.stages.len(),
                self.trajectory.last().map_or(0.0, |s| s.time),
            ));
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            let available = ui.available_size();
            let half_w = available.x / 2.0 - 8.0;
            let half_h = available.y / 2.0 - 8.0;

            ui.horizontal(|ui| {
                // Altitude vs Time
                ui.vertical(|ui| {
                    ui.label("Altitude (km)");
                    let points: PlotPoints =
                        sampled.iter().map(|s| [s.time, s.pos.z / 1000.0]).collect();
                    Plot::new("altitude")
                        .width(half_w)
                        .height(half_h)
                        .x_axis_label("Time (s)")
                        .show(ui, |plot_ui| {
                            plot_ui.line(Line::new("Altitude", points));
                        });
                });

                // Velocity vs Time
                ui.vertical(|ui| {
                    ui.label("Velocity (m/s)");
                    let points: PlotPoints =
                        sampled.iter().map(|s| [s.time, s.vel.norm()]).collect();
                    Plot::new("velocity")
                        .width(half_w)
                        .height(half_h)
                        .x_axis_label("Time (s)")
                        .show(ui, |plot_ui| {
                            plot_ui.line(Line::new("Speed", points));
                        });
                });
            });

            ui.horizontal(|ui| {
                // Pitch angle vs Time
                ui.vertical(|ui| {
                    ui.label("Pitch Angle (deg)");
                    let points: PlotPoints = sampled
                        .iter()
                        .map(|s| [s.time, s.pitch().to_degrees()])
                        .collect();
                    Plot::new("pitch")
                        .width(half_w)
                        .height(half_h)
                        .x_axis_label("Time (s)")
                        .show(ui, |plot_ui| {
                            plot_ui.line(Line::new("Pitch", points));
                        });
                });

                // Altitude vs Downrange
                ui.vertical(|ui| {
                    ui.label("Trajectory Profile (km)");
                    let points: PlotPoints = sampled
                        .iter()
                        .map(|s| {
                            let dr = (s.pos.x.powi(2) + s.pos.y.powi(2)).sqrt();
                            [dr / 1000.0, s.pos.z / 1000.0]
                        })
                        .collect();
                    Plot::new("profile")
                        .width(half_w)
                        .height(half_h)
                        .x_axis_label("Downrange (km)")
                        .data_aspect(1.0)
                        .show(ui, |plot_ui| {
                            plot_ui.line(Line::new("Trajectory", points));
                        });
                });
            });
        });
    }
}
