//! Field map from ONNX v1.22.0 onnx.proto (schema SHA in provenance README).
//! Exactly all top-level message types transitively containing TensorProto.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(super) enum Message {
    Attribute,
    Function,
    Graph,
    Model,
    Node,
    SparseTensor,
    Tensor,
    TrainingInfo,
}
pub(super) fn fields(message: Message) -> &'static [u32] {
    match message {
        Message::Attribute => &[
            1, 21, 13, 20, 2, 3, 4, 5, 6, 22, 14, 7, 8, 9, 10, 11, 23, 15,
        ],
        Message::Function => &[1, 4, 5, 6, 11, 7, 8, 9, 10, 13, 12, 14],
        Message::Graph => &[1, 2, 5, 15, 10, 11, 12, 13, 14, 16],
        Message::Model => &[1, 8, 2, 3, 4, 5, 6, 7, 14, 20, 25, 26],
        Message::Node => &[1, 2, 3, 4, 7, 8, 5, 6, 9, 10],
        Message::SparseTensor => &[1, 2, 3],
        Message::Tensor => &[1, 2, 3, 4, 5, 6, 7, 8, 12, 9, 13, 14, 10, 11, 16],
        Message::TrainingInfo => &[1, 2, 3, 4],
    }
}
pub(super) fn child(message: Message, field: u32) -> Option<(Message, bool)> {
    match (message, field) {
        (Message::Attribute, 5) => Some((Message::Tensor, false)),
        (Message::Attribute, 6) => Some((Message::Graph, false)),
        (Message::Attribute, 22) => Some((Message::SparseTensor, false)),
        (Message::Attribute, 10) => Some((Message::Tensor, true)),
        (Message::Attribute, 11) => Some((Message::Graph, true)),
        (Message::Attribute, 23) => Some((Message::SparseTensor, true)),
        (Message::Function, 11) => Some((Message::Attribute, true)),
        (Message::Function, 7) => Some((Message::Node, true)),
        (Message::Graph, 1) => Some((Message::Node, true)),
        (Message::Graph, 5) => Some((Message::Tensor, true)),
        (Message::Graph, 15) => Some((Message::SparseTensor, true)),
        (Message::Model, 7) => Some((Message::Graph, false)),
        (Message::Model, 20) => Some((Message::TrainingInfo, true)),
        (Message::Model, 25) => Some((Message::Function, true)),
        (Message::Node, 5) => Some((Message::Attribute, true)),
        (Message::SparseTensor, 1) => Some((Message::Tensor, false)),
        (Message::SparseTensor, 2) => Some((Message::Tensor, false)),
        (Message::TrainingInfo, 1) => Some((Message::Graph, false)),
        (Message::TrainingInfo, 2) => Some((Message::Graph, false)),
        _ => None,
    }
}
